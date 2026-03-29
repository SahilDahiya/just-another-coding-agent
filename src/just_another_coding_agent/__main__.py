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
    default_backend_command,
    resolve_go_tui_launch,
)
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.runtime.observability import (
    configure_observability,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    config = load_config()
    with _scoped_config_env(config):
        default_model = resolve_default_model(config)

        parser = argparse.ArgumentParser(
            prog="jaca",
            description="Interactive coding agent with optional headless RPC mode.",
        )
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
        parser.add_argument(
            "--headless",
            action="store_true",
            help="Run as a headless JSON-over-stdio RPC server",
        )
        args = parser.parse_args(list(argv) if argv is not None else None)
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
        )


def _run_headless(
    *,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    input_stream: TextIO | None,
    output_stream: TextIO | None,
) -> int:
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
) -> int:
    launch_command, launch_cwd = resolve_go_tui_launch()
    command = [
        *launch_command,
        "--backend-command-json",
        json.dumps(default_backend_command()),
        "--model",
        model,
        "--workspace-root",
        str(workspace_root),
        "--sessions-root",
        str(sessions_root),
    ]
    if thinking is not None:
        command.extend(["--thinking", thinking])
    completed = subprocess.run(
        command,
        check=False,
        cwd=None if launch_cwd is None else str(launch_cwd),
    )
    return completed.returncode


def _resolve_sessions_root(raw_sessions_root: str | None) -> Path:
    if raw_sessions_root is None:
        default_root = Path.home() / ".jaca" / "sessions"
        default_root.mkdir(parents=True, exist_ok=True)
        return default_root

    sessions_root = Path(raw_sessions_root).expanduser().resolve()
    if sessions_root.exists() and not sessions_root.is_dir():
        raise NotADirectoryError(
            f"Sessions root is not a directory: {sessions_root}"
        )

    sessions_root.mkdir(parents=True, exist_ok=True)
    return sessions_root


@contextmanager
def _scoped_config_env(config: dict[str, str]):
    managed_keys = {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
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
