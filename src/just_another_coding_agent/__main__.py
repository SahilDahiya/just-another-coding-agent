from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from just_another_coding_agent._pdeathsig import set_pdeathsig_in_child
from just_another_coding_agent.auth import get_mcp_server_auth_statuses
from just_another_coding_agent.config import (
    _has_explicit_trace_mode,
    apply_config_to_env,
    apply_trace_mode_to_env,
    load_config,
    load_mcp_server_configs,
    resolve_default_model,
    save_mcp_server_configs,
)
from just_another_coding_agent.contracts.mcp import (
    McpOAuthConfig,
    McpServerConfig,
    McpStdioTransport,
    McpStreamableHttpTransport,
)
from just_another_coding_agent.go_tui import (
    available_installed_update,
    default_backend_command,
    find_go_tui_repo_root,
    go_tui_install_command,
    package_version,
    refresh_cached_release_version_in_background,
    resolve_go_tui_launch,
)
from just_another_coding_agent.mcp_oauth import (
    McpOAuthError,
    clear_mcp_oauth_credentials,
    login_mcp_oauth_server,
)
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import (
    ResolvedSessionReference,
    create_fork,
    list_workspace_sessions,
    resolve_session_reference,
    session_path_for_id,
)
from just_another_coding_agent.runtime.observability import (
    configure_observability,
    flush_observability,
    use_inherited_trace_context,
)
from just_another_coding_agent.session import load_session
from just_another_coding_agent.session.jsonl import SessionFormatError
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.windows_search_tools import (
    apply_managed_tool_path,
    bootstrap_windows_search_tools,
)

_RESUME_PICKER_MAX_SESSIONS = 10
_RESUME_PICKER_LABEL_MAX_CHARS = 72
_RESUME_PICKER_SUBTITLE_MAX_CHARS = 96
_MCP_OAUTH_LOGIN_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class _ResumeSelectionOption:
    label: str
    subtitle: str | None = None


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    config = load_config()
    default_model = resolve_default_model(config)
    subprocess_env = _build_subprocess_env(config)
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    if raw_args and raw_args[0] == "resume":
        return _run_resume_mode(
            argv=raw_args[1:],
            default_model=default_model,
            subprocess_env=subprocess_env,
        )
    if raw_args and raw_args[0] == "fork":
        return _run_fork_mode(
            argv=raw_args[1:],
            default_model=default_model,
            subprocess_env=subprocess_env,
        )
    if raw_args and raw_args[0] == "mcp":
        return _run_mcp_mode(argv=raw_args[1:], output_stream=output_stream)

    parser = argparse.ArgumentParser(
        prog="jaca",
        description="Interactive coding agent with optional headless RPC mode.",
    )
    _add_version_arg(parser)
    _add_common_interactive_args(parser, default_model=default_model)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run as a headless JSON-over-stdio RPC server",
    )
    args = parser.parse_args(raw_args)
    workspace_root = normalize_workspace_root(args.workspace_root)
    sessions_root = _resolve_sessions_root(args.sessions_root)

    if args.headless:
        with _apply_config_env_for_in_process(config):
            return _run_headless(
                model=args.model,
                workspace_root=workspace_root,
                sessions_root=sessions_root,
                input_stream=input_stream,
                output_stream=output_stream,
            )

    return _run_tui(
        model=args.model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=args.thinking,
        env=subprocess_env,
        input_stream=input_stream,
        output_stream=output_stream,
    )


def _run_resume_mode(
    *,
    argv: Sequence[str],
    default_model: str,
    subprocess_env: dict[str, str],
) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca resume",
        description="Resume an existing session by name or opaque session id.",
    )
    _add_version_arg(parser)
    parser.add_argument(
        "session_ref",
        nargs="*",
        help="Normalized session name or opaque session id to resume",
    )
    _add_common_interactive_args(parser, default_model=default_model)
    args = parser.parse_args(list(argv))
    workspace_root = normalize_workspace_root(args.workspace_root)
    sessions_root = _resolve_sessions_root(args.sessions_root)
    if args.session_ref:
        resolved = resolve_session_reference(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_ref=" ".join(args.session_ref),
        )
    else:
        resolved = _select_session_to_resume(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
    forked_from_session_id, forked_from_session_name = _resolve_fork_context(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        resolved=resolved,
    )
    return _run_tui(
        model=args.model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=args.thinking,
        env=subprocess_env,
        session_id=resolved.session_id,
        session_name=resolved.name,
        forked_from_session_id=forked_from_session_id,
        forked_from_session_name=forked_from_session_name,
    )


def _run_fork_mode(
    *,
    argv: Sequence[str],
    default_model: str,
    subprocess_env: dict[str, str],
) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca fork",
        description="Fork an existing session into a new session in this workspace.",
    )
    _add_version_arg(parser)
    parser.add_argument(
        "session_ref",
        nargs="*",
        help="Normalized session name or opaque session id to fork",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Optional name for the new forked session",
    )
    _add_common_interactive_args(parser, default_model=default_model)
    args = parser.parse_args(list(argv))
    workspace_root = normalize_workspace_root(args.workspace_root)
    sessions_root = _resolve_sessions_root(args.sessions_root)
    if args.session_ref:
        source = resolve_session_reference(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_ref=" ".join(args.session_ref),
        )
    else:
        source = _select_session_to_resume(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
    forked = create_fork(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        source_session_id=source.session_id,
        name=args.name,
    )
    return _run_tui(
        model=args.model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=args.thinking,
        env=subprocess_env,
        session_id=forked.session_id,
        session_name=forked.name,
        forked_from_session_id=source.session_id,
        forked_from_session_name=source.name,
    )


def _run_mcp_mode(
    *,
    argv: Sequence[str],
    output_stream: TextIO | None = None,
) -> int:
    writer = sys.stdout if output_stream is None else output_stream
    if argv and argv[0] == "add":
        return _run_mcp_add_argv(argv=argv[1:], writer=writer)

    parser = argparse.ArgumentParser(
        prog="jaca mcp",
        description="Manage configured MCP server authentication.",
    )
    _add_version_arg(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser(
        "add",
        help="Add a configured MCP server",
    )
    add_parser.add_argument("server_id")

    subparsers.add_parser(
        "list",
        help="List configured MCP server auth status",
    )

    login_parser = subparsers.add_parser(
        "login",
        help="Start OAuth login for a configured MCP server",
    )
    login_parser.add_argument("server_id")

    logout_parser = subparsers.add_parser(
        "logout",
        help="Clear OAuth credentials for a configured MCP server",
    )
    logout_parser.add_argument("server_id")

    args = parser.parse_args(list(argv))
    try:
        if args.command == "list":
            return _run_mcp_list(writer=writer)
        config = _load_mcp_cli_server(args.server_id)
        if args.command == "login":
            return _run_mcp_login(config=config, writer=writer)
        if args.command == "logout":
            clear_mcp_oauth_credentials(config)
            writer.write(f"Logged out MCP server {config.server_id}.\n")
            writer.flush()
            return 0
    except Exception as error:
        writer.write(f"Error: {_format_cli_error(error)}\n")
        writer.flush()
        return 1
    raise RuntimeError(f"Unsupported MCP command: {args.command}")


def _run_mcp_list(*, writer: TextIO) -> int:
    statuses = get_mcp_server_auth_statuses()
    if not statuses:
        writer.write("No MCP servers configured.\n")
        writer.flush()
        return 0
    writer.write("server_id\ttransport\tauth\tconfigured\treason\tenv\n")
    for status in statuses:
        env_var = status.env_var if status.env_var is not None else "-"
        writer.write(
            f"{status.server_id}\t{status.transport_type}\t{status.auth_kind}\t"
            f"{str(status.configured).lower()}\t{status.reason}\t{env_var}\n"
        )
    writer.flush()
    return 0


def _run_mcp_add_argv(*, argv: Sequence[str], writer: TextIO) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca mcp add",
        description="Add a configured MCP server.",
    )
    parser.add_argument("server_id")
    parser.add_argument(
        "--url",
        default=None,
        help="Streamable HTTP MCP endpoint URL",
    )
    parser.add_argument(
        "--oauth",
        action="store_true",
        help="Configure OAuth auth and start login after writing config",
    )
    parser.add_argument(
        "--bearer-token-env-var",
        default=None,
        help="Environment variable containing the streamable HTTP bearer token",
    )
    parser.add_argument(
        "--callback-port",
        type=int,
        default=1456,
        help="OAuth callback port for --oauth (default: 1456)",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        help="OAuth scope to request; repeat for multiple scopes",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Static OAuth client id for servers that require one",
    )
    option_argv = list(argv)
    stdio_command: list[str] = []
    if "--" in option_argv:
        separator_index = option_argv.index("--")
        stdio_command = option_argv[separator_index + 1 :]
        option_argv = option_argv[:separator_index]
    args = parser.parse_args(option_argv)
    args.stdio_command = stdio_command
    try:
        return _run_mcp_add(args=args, writer=writer)
    except Exception as error:
        writer.write(f"Error: {_format_cli_error(error)}\n")
        writer.flush()
        return 1


def _run_mcp_add(*, args: argparse.Namespace, writer: TextIO) -> int:
    existing_servers = load_mcp_server_configs()
    config = _build_mcp_add_config(args)
    existing_config = existing_servers.get(config.server_id)
    already_configured = existing_config is not None
    if existing_config is not None and existing_config != config:
        raise RuntimeError(
            f"MCP server already exists with different config: {args.server_id}"
        )
    updated_servers = dict(existing_servers)
    updated_servers[config.server_id] = config
    if not already_configured:
        save_mcp_server_configs(updated_servers)

    transport = config.transport
    if (
        isinstance(transport, McpStreamableHttpTransport)
        and transport.oauth is not None
    ):
        try:
            _perform_mcp_login(config=config, writer=writer)
        except Exception as error:
            writer.write(
                f"MCP server {config.server_id} was added, "
                f"but OAuth login failed: {_format_cli_error(error)}\n"
            )
            writer.write(f"Run `jaca mcp login {config.server_id}` to retry.\n")
            writer.flush()
            return 1
        if already_configured:
            writer.write(
                f"MCP server {config.server_id} already configured; "
                "OAuth login refreshed.\n"
            )
        else:
            writer.write(f"Added and logged in MCP server {config.server_id}.\n")
        writer.flush()
        return 0

    if already_configured:
        writer.write(f"MCP server {config.server_id} already configured.\n")
        writer.flush()
        return 0

    if isinstance(transport, McpStreamableHttpTransport):
        if transport.bearer_token_env_var is not None:
            writer.write(
                f"Added MCP server {config.server_id}. "
                f"Set {transport.bearer_token_env_var} before use.\n"
            )
        else:
            writer.write(f"Added MCP server {config.server_id}.\n")
    else:
        writer.write(f"Added MCP server {config.server_id}.\n")
    writer.flush()
    return 0


def _build_mcp_add_config(args: argparse.Namespace) -> McpServerConfig:
    stdio_command = list(args.stdio_command)
    if stdio_command and stdio_command[0] == "--":
        stdio_command = stdio_command[1:]
    has_stdio = bool(stdio_command)
    has_url = args.url is not None
    if has_url == has_stdio:
        raise RuntimeError("MCP add requires exactly one of --url or stdio command")
    if args.oauth and args.bearer_token_env_var is not None:
        raise RuntimeError("--oauth and --bearer-token-env-var are mutually exclusive")
    if has_stdio:
        if args.oauth or args.bearer_token_env_var is not None:
            raise RuntimeError("stdio MCP servers do not support HTTP auth options")
        return McpServerConfig(
            server_id=args.server_id,
            transport=McpStdioTransport(
                command=stdio_command[0],
                args=stdio_command[1:],
            ),
        )
    oauth = (
        McpOAuthConfig(
            callback_port=args.callback_port,
            scopes=args.scope,
            client_id=args.client_id,
        )
        if args.oauth
        else None
    )
    return McpServerConfig(
        server_id=args.server_id,
        transport=McpStreamableHttpTransport(
            url=args.url,
            bearer_token_env_var=args.bearer_token_env_var,
            oauth=oauth,
        ),
    )


def _load_mcp_cli_server(server_id: str):
    servers = load_mcp_server_configs()
    try:
        return servers[server_id]
    except KeyError as error:
        raise RuntimeError(f"Unknown MCP server: {server_id}") from error


def _run_mcp_login(*, config, writer: TextIO) -> int:
    transport = config.transport
    if not isinstance(transport, McpStreamableHttpTransport):
        raise RuntimeError("MCP OAuth login requires streamable_http transport")
    if transport.oauth is None:
        raise RuntimeError("MCP server does not have OAuth configured")

    result = _perform_mcp_login(config=config, writer=writer)
    writer.write(f"Logged in MCP server {result.server_id}.\n")
    writer.flush()
    return 0


def _perform_mcp_login(*, config: McpServerConfig, writer: TextIO):
    transport = config.transport
    if not isinstance(transport, McpStreamableHttpTransport):
        raise RuntimeError("MCP OAuth login requires streamable_http transport")
    if transport.oauth is None:
        raise RuntimeError("MCP server does not have OAuth configured")

    async def auth_url_handler(auth_url: str) -> None:
        writer.write(f"Open this URL to authenticate {config.server_id}:\n")
        writer.write(f"{auth_url}\n")
        writer.flush()

    async def connect(client) -> None:
        from pydantic_ai.mcp import MCPServerStreamableHTTP

        server = MCPServerStreamableHTTP(
            transport.url,
            http_client=client,
            id=config.server_id,
            tool_prefix=None,
            timeout=max(
                config.startup_timeout_sec or 0,
                _MCP_OAUTH_LOGIN_TIMEOUT_SECONDS,
            ),
            read_timeout=max(
                config.tool_timeout_sec or 0,
                _MCP_OAUTH_LOGIN_TIMEOUT_SECONDS,
            ),
            allow_sampling=False,
            max_retries=0,
        )
        async with server:
            pass

    try:
        return asyncio.run(
            login_mcp_oauth_server(
                config,
                auth_url_handler=auth_url_handler,
                connect=connect,
            )
        )
    except McpOAuthError:
        raise


def _format_cli_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        leaf_messages = [
            _format_cli_error(leaf) for leaf in _flatten_exception_group(error)
        ]
        unique_messages = list(
            dict.fromkeys(message for message in leaf_messages if message)
        )
        if unique_messages:
            return "; ".join(unique_messages)
    return str(error) or type(error).__name__


def _flatten_exception_group(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        flattened: list[BaseException] = []
        for nested in error.exceptions:
            flattened.extend(_flatten_exception_group(nested))
        return flattened
    return [error]


def _select_session_to_resume(
    *,
    sessions_root: Path,
    workspace_root: Path,
) -> ResolvedSessionReference:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "jaca resume without a session reference requires an interactive terminal"
        )

    sessions = list_workspace_sessions(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    if not sessions:
        raise RuntimeError(f"No sessions found for workspace: {workspace_root}")

    displayed_sessions = sessions[:_RESUME_PICKER_MAX_SESSIONS]
    options = _build_resume_selection_options(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        sessions=displayed_sessions,
    )
    with _resume_picker_input_mode():
        selected_index = _pick_resume_selection(
            options,
            writer=sys.stdout,
            key_reader=_read_resume_picker_key,
        )
    selected = displayed_sessions[selected_index]
    selected_option = options[selected_index]
    return ResolvedSessionReference(
        session_id=selected.session_id,
        name=selected.name or selected_option.label,
    )


def _build_resume_selection_options(
    *,
    sessions_root: Path,
    workspace_root: Path,
    sessions: Sequence[object],
) -> list[_ResumeSelectionOption]:
    options: list[_ResumeSelectionOption] = []
    for session in sessions:
        label_text = session.name
        if label_text is None:
            label_text = _first_prompt_for_session(
                sessions_root=sessions_root,
                workspace_root=workspace_root,
                session_id=session.session_id,
            )
        label_text = label_text or "Unnamed session"
        subtitle_text = _format_resume_timestamp(session.updated_at)
        options.append(
            _ResumeSelectionOption(
                label=_truncate_resume_text(
                    label_text,
                    max_chars=_RESUME_PICKER_LABEL_MAX_CHARS,
                ),
                subtitle=_truncate_resume_text(
                    subtitle_text,
                    max_chars=_RESUME_PICKER_SUBTITLE_MAX_CHARS,
                )
                if subtitle_text
                else None,
            )
        )
    return options


def _first_prompt_for_session(
    *,
    sessions_root: Path,
    workspace_root: Path,
    session_id: str,
) -> str | None:
    path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    try:
        loaded = load_session(path=path, workspace_root=workspace_root)
    except SessionFormatError:
        return None
    for run in loaded.runs:
        prompt = _truncate_resume_text(
            run.prompt,
            max_chars=_RESUME_PICKER_SUBTITLE_MAX_CHARS,
        )
        if prompt:
            return prompt
    return None


def _format_resume_timestamp(updated_at) -> str:
    return "updated " + updated_at.astimezone().strftime("%Y-%m-%d %H:%M")


def _truncate_resume_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if normalized == "":
        return ""
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def _pick_resume_selection(
    options: Sequence[_ResumeSelectionOption],
    *,
    writer: TextIO,
    key_reader: Callable[[], str],
) -> int:
    if not options:
        raise RuntimeError("Resume selection requires at least one session")

    selected_index = 0
    rendered_line_count = 0
    while True:
        rendered_line_count = _render_resume_picker(
            options,
            selected_index=selected_index,
            writer=writer,
            previous_line_count=rendered_line_count,
        )
        key = key_reader()
        if key == "up":
            selected_index = (selected_index - 1) % len(options)
        elif key == "down":
            selected_index = (selected_index + 1) % len(options)
        elif key == "enter":
            if rendered_line_count:
                writer.write(f"\x1b[{rendered_line_count}F")
                writer.write("\x1b[J")
                writer.flush()
            return selected_index


def _render_resume_picker(
    options: Sequence[_ResumeSelectionOption],
    *,
    selected_index: int,
    writer: TextIO,
    previous_line_count: int,
) -> int:
    if previous_line_count:
        writer.write(f"\x1b[{previous_line_count}F")
        writer.write("\x1b[J")

    lines = [
        "Recent sessions",
        "Use up/down arrows, then press Enter to resume.",
        "",
    ]
    for index, option in enumerate(options):
        prefix = ">" if index == selected_index else " "
        lines.append(f"{prefix} {option.label}")
        if option.subtitle:
            lines.append(f"  {option.subtitle}")
        if index != len(options) - 1:
            lines.append("")

    writer.write("\r\n".join(lines))
    writer.write("\r\n")
    writer.flush()
    return len(lines)


@contextmanager
def _resume_picker_input_mode():
    if os.name == "nt":
        yield
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def _read_resume_picker_key() -> str:
    if os.name == "nt":
        return _read_resume_picker_key_windows()
    return _read_resume_picker_key_posix()


def _read_resume_picker_key_windows() -> str:
    import msvcrt

    first = msvcrt.getwch()
    if first in ("\r", "\n"):
        return "enter"
    if first in ("k", "K"):
        return "up"
    if first in ("j", "J"):
        return "down"
    if first == "\x03":
        raise KeyboardInterrupt
    if first in ("\x00", "\xe0"):
        second = msvcrt.getwch()
        if second == "H":
            return "up"
        if second == "P":
            return "down"
    return "other"


def _read_resume_picker_key_posix() -> str:
    first = sys.stdin.read(1)
    if first in ("\r", "\n"):
        return "enter"
    if first in ("k", "K"):
        return "up"
    if first in ("j", "J"):
        return "down"
    if first == "\x03":
        raise KeyboardInterrupt
    if first != "\x1b":
        return "other"
    second = sys.stdin.read(1)
    if second not in ("[", "O"):
        return "other"
    third = sys.stdin.read(1)
    sequence = second + third
    if second == "[" and not ("@" <= third <= "~"):
        while len(sequence) < 8:
            next_char = sys.stdin.read(1)
            if next_char == "":
                break
            sequence += next_char
            if "@" <= next_char <= "~":
                break
    return _decode_resume_picker_escape_sequence(sequence)


def _decode_resume_picker_escape_sequence(sequence: str) -> str:
    if sequence in ("[A", "OA"):
        return "up"
    if sequence in ("[B", "OB"):
        return "down"
    if sequence.startswith("[") and sequence.endswith("A"):
        return "up"
    if sequence.startswith("[") and sequence.endswith("B"):
        return "down"
    return "other"


def _add_common_interactive_args(
    parser: argparse.ArgumentParser,
    *,
    default_model: str,
) -> None:
    parser.add_argument(
        "--model",
        default=default_model,
        help=f"Model to use (default: {default_model}, or set JACA_MODEL env var)",
    )
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--sessions-root",
        default=None,
        help="Sessions storage directory (default: ~/.jaca/sessions)",
    )
    parser.add_argument(
        "--thinking",
        choices=["true", "false", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
    )


def _add_version_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )


def _run_headless(
    *,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    input_stream: TextIO | None,
    output_stream: TextIO | None,
) -> int:
    rpc_input_stream = sys.stdin if input_stream is None else input_stream
    rpc_output_stream = sys.stdout if output_stream is None else output_stream
    with redirect_stdout(sys.stderr):
        apply_managed_tool_path()
        configure_observability()
        try:
            with use_inherited_trace_context():
                asyncio.run(
                    serve_rpc_stdio(
                        input_stream=rpc_input_stream,
                        output_stream=rpc_output_stream,
                        model=model,
                        workspace_root=workspace_root,
                        sessions_root=sessions_root,
                    )
                )
        except KeyboardInterrupt:
            return 130
        finally:
            flush_observability()
    return 0


def _run_tui(
    *,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    thinking: str | None,
    env: dict[str, str] | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    session_id: str | None = None,
    session_name: str | None = None,
    forked_from_session_id: str | None = None,
    forked_from_session_name: str | None = None,
) -> int:
    launch_command, launch_cwd = resolve_go_tui_launch()
    app_version = package_version()
    update = available_installed_update(
        repo_root=launch_cwd,
        current_version=app_version,
    )
    refresh_cached_release_version_in_background(repo_root=launch_cwd)
    bootstrap_windows_search_tools(
        writer=sys.stdout if output_stream is None else output_stream
    )
    command = [
        *launch_command,
        "--backend-command-json",
        json.dumps(default_backend_command()),
        "--app-version",
        app_version,
    ]
    if update is not None:
        command.extend(
            [
                "--available-update-version",
                update.latest_version,
                "--available-update-command-json",
                json.dumps(list(update.command)),
            ]
        )
    command.extend(
        [
            "--model",
            model,
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )
    if thinking is not None:
        command.extend(["--thinking", thinking])
    if session_id is not None:
        command.extend(["--session-id", session_id])
    if session_name is not None:
        command.extend(["--session-name", session_name])
    if forked_from_session_id is not None:
        command.extend(["--forked-from-session-id", forked_from_session_id])
    if forked_from_session_name is not None:
        command.extend(["--forked-from-session-name", forked_from_session_name])
    subprocess_kwargs: dict[str, object] = dict(
        check=False,
        cwd=None if launch_cwd is None else str(launch_cwd),
        env=env,
    )
    # preexec_fn is POSIX-only; on Windows passing it raises ValueError and
    # the launch-block detection below handles the parallel concern there.
    # On Linux we set PR_SET_PDEATHSIG=SIGTERM so the Go TUI cannot outlive
    # this wrapper under any failure mode (abandoned PTY, SIGKILL, crash).
    if os.name != "nt":
        subprocess_kwargs["preexec_fn"] = set_pdeathsig_in_child
    try:
        completed = subprocess.run(command, **subprocess_kwargs)
    except OSError as error:
        if _is_windows_launch_policy_error(error, launch_command=launch_command):
            raise RuntimeError(
                _format_windows_launch_policy_error(
                    error=error,
                    launch_command=launch_command,
                )
            ) from error
        raise
    return completed.returncode


def _is_windows_launch_policy_error(
    error: OSError,
    *,
    launch_command: Sequence[str],
) -> bool:
    if os.name != "nt" or not launch_command:
        return False
    executable = launch_command[0].lower()
    if executable == "go":
        return False
    winerror = getattr(error, "winerror", None)
    message = " ".join(
        part
        for part in [str(error), getattr(error, "strerror", "")]
        if isinstance(part, str) and part
    ).lower()
    if winerror in {216, 225}:
        return True
    return (
        "application control" in message
        or "malicious binary reputation" in message
        or "smart app control" in message
        or ("blocked" in message and ".exe" in executable)
    )


def _format_windows_launch_policy_error(
    *,
    error: OSError,
    launch_command: Sequence[str],
) -> str:
    repo_root = find_go_tui_repo_root()
    repair_command = go_tui_install_command(repo_root=repo_root)
    binary_path = launch_command[0]
    lines = [
        "Windows blocked the JACA Go TUI executable before launch.",
        (
            "This is usually Microsoft Defender, Windows Application Control, "
            "or Smart App Control blocking an unsigned or untrusted executable."
        ),
        f"Blocked executable: {binary_path}",
        f"Original error: {error}",
        f"Repair command: {repair_command}",
    ]
    if repo_root is not None and shutil.which("go") is not None:
        lines.append("Repo workaround: JACA_GO_RUN=1 uv run jaca")
    lines.append(
        "Release fix: publish a verified Windows wheel whose bundled "
        "jaca-go.exe launches after uv tool install."
    )
    return "\n".join(lines)


def _resolve_fork_context(
    *,
    sessions_root: Path,
    workspace_root: Path,
    resolved: ResolvedSessionReference,
) -> tuple[str | None, str | None]:
    if resolved.forked_from_session_id is None:
        return None, None
    parent = resolve_session_reference(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_ref=resolved.forked_from_session_id,
    )
    return parent.session_id, parent.name


def _resolve_sessions_root(raw_sessions_root: str | None) -> Path:
    if raw_sessions_root is None:
        default_root = Path.home() / ".jaca" / "sessions"
        default_root.mkdir(parents=True, exist_ok=True)
        return default_root

    sessions_root = Path(raw_sessions_root).expanduser().resolve()
    if sessions_root.exists() and not sessions_root.is_dir():
        raise NotADirectoryError(f"Sessions root is not a directory: {sessions_root}")

    sessions_root.mkdir(parents=True, exist_ok=True)
    return sessions_root


def _build_subprocess_env(config: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    if "OPENAI_BASE_URL" in config and "OPENAI_BASE_URL" not in env:
        env["OPENAI_BASE_URL"] = config["OPENAI_BASE_URL"]
    if _has_explicit_trace_mode(env):
        return env
    env.pop("JACA_TRACE_MODE", None)
    trace_mode = config.get("trace_mode", "").strip().lower()
    if trace_mode == "":
        return env
    if trace_mode == "off":
        env.pop("JACA_TRACE_MODE", None)
    elif trace_mode in {"local", "logfire"}:
        env["JACA_TRACE_MODE"] = trace_mode
    else:
        raise RuntimeError(
            "Invalid trace_mode in ~/.jaca/config.json: expected off, local, or logfire"
        )
    return env


@contextmanager
def _apply_config_env_for_in_process(config: dict[str, str]):
    managed_keys = {"OPENAI_BASE_URL", "JACA_TRACE_MODE"}
    original_env = {key: os.environ.get(key) for key in managed_keys}
    apply_config_to_env(config)
    apply_trace_mode_to_env(config)
    try:
        yield
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
