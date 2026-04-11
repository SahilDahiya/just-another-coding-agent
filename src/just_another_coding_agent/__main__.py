from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from just_another_coding_agent.config import (
    apply_config_to_env,
    apply_trace_mode_to_env,
    load_config,
    resolve_default_model,
)
from just_another_coding_agent.go_tui import (
    available_installed_update,
    default_backend_command,
    package_version,
    resolve_go_tui_launch,
)
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import (
    ResolvedSessionReference,
    create_fork,
    list_workspace_sessions,
    resolve_session_reference,
)
from just_another_coding_agent.runtime.observability import (
    configure_observability,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.windows_search_tools import (
    apply_managed_tool_path,
    bootstrap_windows_search_tools,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    config = load_config()
    with _scoped_config_env(config):
        default_model = resolve_default_model(config)
        raw_args = list(argv) if argv is not None else sys.argv[1:]

        if raw_args and raw_args[0] == "resume":
            return _run_resume_mode(
                argv=raw_args[1:],
                default_model=default_model,
            )
        if raw_args and raw_args[0] == "fork":
            return _run_fork_mode(
                argv=raw_args[1:],
                default_model=default_model,
            )

        parser = argparse.ArgumentParser(
            prog="jaca",
            description="Interactive coding agent with optional headless RPC mode.",
        )
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
            input_stream=input_stream,
            output_stream=output_stream,
        )


def _run_resume_mode(
    *,
    argv: Sequence[str],
    default_model: str,
) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca resume",
        description="Resume an existing session by name or opaque session id.",
    )
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
        session_id=resolved.session_id,
        session_name=resolved.name,
        forked_from_session_id=forked_from_session_id,
        forked_from_session_name=forked_from_session_name,
    )


def _run_fork_mode(
    *,
    argv: Sequence[str],
    default_model: str,
) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca fork",
        description="Fork an existing session into a new session in this workspace.",
    )
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
        session_id=forked.session_id,
        session_name=forked.name,
        forked_from_session_id=source.session_id,
        forked_from_session_name=source.name,
    )


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

    displayed_sessions = sessions[:10]
    print("Recent sessions:")
    if len(sessions) > len(displayed_sessions):
        print(
            f"Showing {len(displayed_sessions)} most recent "
            f"of {len(sessions)} sessions."
        )
    for index, session in enumerate(displayed_sessions, start=1):
        label = session.name or session.session_id
        print(f"{index}. {label}")
        if session.name is not None:
            print(f"   id: {session.session_id}")

    raw_choice = input(
        f"Select session [1-{len(displayed_sessions)}, default 1]: "
    ).strip()
    if raw_choice == "":
        selected = displayed_sessions[0]
        return ResolvedSessionReference(
            session_id=selected.session_id,
            name=selected.name,
        )
    try:
        selected_index = int(raw_choice)
    except ValueError as error:
        raise RuntimeError("Resume selection must be a session number") from error
    if selected_index < 1 or selected_index > len(displayed_sessions):
        raise RuntimeError("Resume selection is out of range")
    selected = displayed_sessions[selected_index - 1]
    return ResolvedSessionReference(
        session_id=selected.session_id,
        name=selected.name,
    )


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


def _run_headless(
    *,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    input_stream: TextIO | None,
    output_stream: TextIO | None,
) -> int:
    apply_managed_tool_path()
    configure_observability()
    try:
        asyncio.run(
            serve_rpc_stdio(
                input_stream=sys.stdin if input_stream is None else input_stream,
                output_stream=sys.stdout if output_stream is None else output_stream,
                model=model,
                workspace_root=workspace_root,
                sessions_root=sessions_root,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


def _run_tui(
    *,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    thinking: str | None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    session_id: str | None = None,
    session_name: str | None = None,
    forked_from_session_id: str | None = None,
    forked_from_session_name: str | None = None,
) -> int:
    launch_command, launch_cwd = resolve_go_tui_launch()
    app_version = package_version()
    if not _prompt_for_external_update_if_needed(
        repo_root=launch_cwd,
        current_version=app_version,
        input_stream=input_stream,
        output_stream=output_stream,
    ):
        return 0
    bootstrap_windows_search_tools(
        writer=sys.stdout if output_stream is None else output_stream
    )
    command = [
        *launch_command,
        "--backend-command-json",
        json.dumps(default_backend_command()),
        "--app-version",
        app_version,
        "--model",
        model,
        "--workspace-root",
        str(workspace_root),
        "--sessions-root",
        str(sessions_root),
    ]
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
    completed = subprocess.run(
        command,
        check=False,
        cwd=None if launch_cwd is None else str(launch_cwd),
    )
    return completed.returncode


def _prompt_for_external_update_if_needed(
    *,
    repo_root: Path | None,
    current_version: str,
    input_stream: TextIO | None,
    output_stream: TextIO | None,
) -> bool:
    reader = sys.stdin if input_stream is None else input_stream
    writer = sys.stdout if output_stream is None else output_stream
    if not _stream_is_tty(reader) or not _stream_is_tty(writer):
        return True

    update = available_installed_update(
        current_version=current_version,
        repo_root=repo_root,
    )
    if update is None:
        return True

    writer.write(
        "\n".join(
            [
                "Update available: "
                f"{update.current_version} -> {update.latest_version}",
                "To update, do it outside the running app with:",
                f"  {' '.join(update.command)}",
                "Press Enter to continue, or type 'q' to quit and update now: ",
            ]
        )
    )
    writer.flush()
    response = reader.readline().strip().lower()
    writer.write("\n")
    writer.flush()
    return response != "q"


def _stream_is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


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


@contextmanager
def _scoped_config_env(config: dict[str, str]):
    managed_keys = {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "JACA_TRACE_MODE",
    }
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
