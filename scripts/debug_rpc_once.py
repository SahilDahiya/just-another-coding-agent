from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from just_another_coding_agent.__main__ import main
from just_another_coding_agent.contracts.rpc import RpcResponseEnvelope


def _run_backend_once(
    *,
    argv: list[str],
    request_payloads: list[dict[str, object]],
) -> str:
    input_stream = io.StringIO(
        "\n".join(json.dumps(payload) for payload in request_payloads) + "\n"
    )
    output_stream = io.StringIO()
    exit_code = main(argv=argv, input_stream=input_stream, output_stream=output_stream)
    if exit_code != 0:
        raise SystemExit(exit_code)
    return output_stream.getvalue()


def _extract_session_id(create_output: str) -> str:
    for line in create_output.splitlines():
        payload = json.loads(line)
        if payload.get("type") != "rpc_response":
            continue
        if payload.get("id") != "req-create":
            continue
        envelope = RpcResponseEnvelope.model_validate(payload)
        response = envelope.response
        if isinstance(response, dict):
            session_id = response.get("session_id")
            if isinstance(session_id, str):
                return session_id
        session_id = getattr(response, "session_id", None)
        if isinstance(session_id, str):
            return session_id
    raise RuntimeError("debug_rpc_once did not receive a session.create response")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one headless RPC session.create + run.start cycle for debugging."
        )
    )
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompt_list",
        default=[],
        help="Prompt to send to run.start. Repeat to continue the same session.",
    )
    parser.add_argument(
        "--prompts",
        default=None,
        help="Multiple prompts separated by '||' or newlines.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model id to pass to the backend, e.g. openai-responses:gpt-5.4",
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


def _resolve_prompts(args: argparse.Namespace) -> list[str]:
    prompt_list = getattr(args, "prompt_list", None)
    if prompt_list is None:
        legacy_prompt = getattr(args, "prompt", None)
        prompt_list = [legacy_prompt] if isinstance(legacy_prompt, str) else []
    prompts = [prompt for prompt in prompt_list if prompt.strip()]
    prompts_arg = getattr(args, "prompts", None)
    if prompts_arg is not None:
        split_prompts = [
            prompt.strip()
            for prompt in prompts_arg.replace("\r\n", "\n").split("||")
            for prompt in prompt.splitlines()
            if prompt.strip()
        ]
        prompts.extend(split_prompts)
    if not prompts:
        raise RuntimeError("Provide at least one --prompt or a non-empty --prompts")
    return prompts


def main_cli() -> int:
    args = parse_args()
    prompts = _resolve_prompts(args)
    argv = [
        "--headless",
        "--model",
        args.model,
        "--workspace-root",
        str(Path(args.workspace_root).resolve()),
    ]
    if args.sessions_root is not None:
        argv.extend(["--sessions-root", str(Path(args.sessions_root).resolve())])
    create_output = _run_backend_once(
        argv=argv,
        request_payloads=[
            {
                "id": "req-create",
                "command": "session.create",
                "payload": {},
            }
        ],
    )
    print(create_output, end="")
    session_id = _extract_session_id(create_output)
    for index, prompt in enumerate(prompts, start=1):
        request_id = "req-run" if len(prompts) == 1 else f"req-run-{index}"
        run_output = _run_backend_once(
            argv=argv,
            request_payloads=[
                {
                    "id": request_id,
                    "command": "run.start",
                    "payload": {
                        "session_id": session_id,
                        "prompt": prompt,
                        "thinking": args.thinking,
                    },
                }
            ],
        )
        print(run_output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
