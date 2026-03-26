from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import TextIO

from pi_code_agent.rpc import serve_rpc_stdio
from pi_code_agent.tools._workspace import normalize_workspace_root


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="pi-code-agent",
        description="Serve the coding-agent JSON-over-stdio RPC backend.",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--sessions-root", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    workspace_root = normalize_workspace_root(args.workspace_root)

    asyncio.run(
        serve_rpc_stdio(
            input_stream=sys.stdin if input_stream is None else input_stream,
            output_stream=sys.stdout if output_stream is None else output_stream,
            model=args.model,
            workspace_root=workspace_root,
            sessions_root=args.sessions_root,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
