from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.tools._workspace import normalize_workspace_root


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="just-another-coding-agent",
        description="Serve the coding-agent JSON-over-stdio RPC backend.",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--sessions-root", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    workspace_root = normalize_workspace_root(args.workspace_root)
    sessions_root = _prepare_sessions_root(args.sessions_root)

    try:
        asyncio.run(
            serve_rpc_stdio(
                input_stream=sys.stdin if input_stream is None else input_stream,
                output_stream=sys.stdout if output_stream is None else output_stream,
                model=args.model,
                workspace_root=workspace_root,
                sessions_root=sessions_root,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


def _prepare_sessions_root(raw_sessions_root: str) -> Path:
    sessions_root = Path(raw_sessions_root).expanduser().resolve()
    if sessions_root.exists() and not sessions_root.is_dir():
        raise NotADirectoryError(
            f"Sessions root is not a directory: {sessions_root}"
        )

    sessions_root.mkdir(parents=True, exist_ok=True)
    return sessions_root


if __name__ == "__main__":
    raise SystemExit(main())
