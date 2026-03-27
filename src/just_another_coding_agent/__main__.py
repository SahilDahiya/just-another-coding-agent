from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tui.config import apply_config_to_env, load_config

apply_config_to_env(load_config())

DEFAULT_MODEL = os.environ.get("JACA_MODEL", "ollama:kimi-k2:1t-cloud")


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca",
        description="Interactive coding agent with optional headless RPC mode.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL}, or set JACA_MODEL env var)",
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
    from just_another_coding_agent.tui import CodingAgentApp

    app = CodingAgentApp(
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=thinking,
    )
    app.run()
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
