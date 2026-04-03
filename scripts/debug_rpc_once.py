from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from just_another_coding_agent.__main__ import main


def _build_rpc_input(*, prompt: str, thinking: str) -> io.StringIO:
    payloads = [
        {
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        {
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": "debug-session",
                "prompt": prompt,
                "thinking": thinking,
            },
        },
    ]
    return io.StringIO("\n".join(json.dumps(payload) for payload in payloads) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one headless RPC session.create + run.start cycle for debugging."
    )
    parser.add_argument("--prompt", required=True, help="Prompt to send to run.start")
    parser.add_argument(
        "--model",
        required=True,
        help="Model id to pass to the backend, e.g. openai:gpt-5.4",
    )
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root passed to the backend",
    )
    parser.add_argument(
        "--sessions-root",
        default=None,
        help="Optional sessions root passed to the backend",
    )
    parser.add_argument(
        "--thinking",
        default="medium",
        choices=("low", "medium", "high"),
        help="Thinking level for run.start",
    )
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    input_stream = _build_rpc_input(prompt=args.prompt, thinking=args.thinking)
    output_stream = io.StringIO()
    argv = [
        "--headless",
        "--model",
        args.model,
        "--workspace-root",
        str(Path(args.workspace_root).resolve()),
    ]
    if args.sessions_root is not None:
        argv.extend(["--sessions-root", str(Path(args.sessions_root).resolve())])
    exit_code = main(argv=argv, input_stream=input_stream, output_stream=output_stream)
    print(output_stream.getvalue(), end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main_cli())
