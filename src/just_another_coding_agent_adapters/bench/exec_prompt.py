from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TextIO

from just_another_coding_agent.contracts.thinking import ThinkingSetting


class ExecPromptError(RuntimeError):
    """Raised when the one-shot wrapper cannot complete a canonical run."""


BENCHMARK_WORKFLOW_PROMPT = "\n".join(
    [
        "# Benchmark Workflow",
        "",
        "- Prefer provided tests or verifier files over ad-hoc smoke checks.",
        (
            "- When relevant tests exist, run the narrowest failing test or repro "
            "before editing when feasible."
        ),
        (
            "- For behavioral tasks, syntax, import, and compile checks are not "
            "sufficient."
        ),
        (
            "- After changes, rerun the same targeted test or acceptance check "
            "before concluding."
        ),
        (
            "- If no tests exist, run the smallest concrete acceptance check that "
            "exercises the required behavior."
        ),
    ]
)


def build_server_command(
    *,
    model: str,
    workspace_root: Path | str,
    sessions_root: Path | str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "just_another_coding_agent",
        "--model",
        model,
        "--workspace-root",
        str(Path(workspace_root).expanduser().resolve()),
        "--sessions-root",
        str(Path(sessions_root).expanduser().resolve()),
    ]


def read_prompt(prompt_arg: str | None, *, stdin: TextIO) -> str:
    if prompt_arg and prompt_arg != "-":
        return prompt_arg

    if stdin.isatty():
        raise SystemExit("Provide a prompt as an argument or pipe it on stdin.")

    prompt = stdin.read()
    if not prompt.strip():
        raise SystemExit("Prompt is empty.")
    return prompt


def build_benchmark_prompt(prompt: str) -> str:
    return f"{BENCHMARK_WORKFLOW_PROMPT}\n\n# Task\n{prompt}"


def run_exec_prompt(
    *,
    prompt: str,
    model: str,
    workspace_root: Path | str,
    thinking: ThinkingSetting | None = None,
    sessions_root: Path | str | None = None,
    popen_factory: Any = subprocess.Popen,
) -> str:
    resolved_workspace_root = Path(workspace_root).expanduser().resolve()
    if not resolved_workspace_root.is_dir():
        raise ExecPromptError(f"Directory does not exist: {resolved_workspace_root}")

    if sessions_root is None:
        with TemporaryDirectory(
            prefix="just-another-coding-agent-sessions."
        ) as temporary_root:
            return _run_exec_prompt(
                prompt=prompt,
                model=model,
                workspace_root=resolved_workspace_root,
                thinking=thinking,
                sessions_root=Path(temporary_root),
                popen_factory=popen_factory,
            )

    return _run_exec_prompt(
        prompt=prompt,
        model=model,
        workspace_root=resolved_workspace_root,
        thinking=thinking,
        sessions_root=Path(sessions_root).expanduser().resolve(),
        popen_factory=popen_factory,
    )


def _run_exec_prompt(
    *,
    prompt: str,
    model: str,
    workspace_root: Path,
    thinking: ThinkingSetting | None,
    sessions_root: Path,
    popen_factory: Any,
) -> str:
    command = build_server_command(
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    process = popen_factory(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    if process.stdin is None or process.stdout is None:
        raise ExecPromptError(
            "just-another-coding-agent subprocess must expose stdin and stdout"
        )

    try:
        _write_json_line(
            process.stdin,
            {"id": "req-create", "command": "session.create", "payload": {}},
        )
        session_create_response = _read_json_line(
            process.stdout,
            expected="session.create response",
        )
        session_id = _extract_session_id(session_create_response)

        _write_json_line(
            process.stdin,
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": session_id,
                    "prompt": build_benchmark_prompt(prompt),
                    "thinking": thinking,
                },
            },
        )

        while True:
            response = _read_json_line(process.stdout, expected="run.start response")
            response_type = response.get("type")

            if response_type == "rpc_error":
                raise ExecPromptError(
                    f"{response.get('error_type')}: {response.get('message')}"
                )

            if response_type != "rpc_event":
                raise ExecPromptError(
                    f"Unexpected RPC response type: {response_type!r}"
                )

            event = response.get("event")
            if not isinstance(event, dict):
                raise ExecPromptError("rpc_event must include an event object")

            event_type = event.get("type")
            if event_type == "run_succeeded":
                output_text = event.get("output_text")
                if not isinstance(output_text, str):
                    raise ExecPromptError(
                        "run_succeeded must include string output_text"
                    )
                return output_text

            if event_type == "run_failed":
                raise ExecPromptError(
                    f"{event.get('error_type')}: {event.get('message')}"
                )
    finally:
        process.stdin.close()
        _wait_for_process(process)


def _write_json_line(stream: TextIO, payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload))
    stream.write("\n")
    stream.flush()


def _read_json_line(stream: TextIO, *, expected: str) -> dict[str, object]:
    line = stream.readline()
    if line == "":
        raise ExecPromptError(f"EOF while waiting for {expected}")

    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise ExecPromptError(f"Invalid JSON while waiting for {expected}") from error

    if not isinstance(payload, dict):
        raise ExecPromptError(f"{expected} must be a JSON object")
    return payload


def _extract_session_id(payload: dict[str, object]) -> str:
    if payload.get("type") == "rpc_error":
        raise ExecPromptError(
            f"{payload.get('error_type')}: {payload.get('message')}"
        )

    if payload.get("type") != "rpc_response":
        raise ExecPromptError("session.create must return rpc_response")

    response = payload.get("response")
    if not isinstance(response, dict):
        raise ExecPromptError(
            "session.create rpc_response must include response object"
        )

    session_id = response.get("session_id")
    if not isinstance(session_id, str):
        raise ExecPromptError("session.create response must include string session_id")
    return session_id


def _wait_for_process(process: Any) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    parser = argparse.ArgumentParser(
        description=(
            "Run one prompt through the canonical "
            "just-another-coding-agent stdio backend."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Prompt to answer. Use '-' or omit this argument to read from stdin.",
    )
    parser.add_argument("-C", "--cd", default=".")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--thinking",
        choices=["true", "false", "minimal", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument("--sessions-root")
    args = parser.parse_args(list(argv) if argv is not None else None)

    prompt = read_prompt(args.prompt, stdin=input_stream)
    try:
        output = run_exec_prompt(
            prompt=prompt,
            model=args.model,
            workspace_root=args.cd,
            thinking=_parse_thinking(args.thinking),
            sessions_root=args.sessions_root,
        )
    except ExecPromptError as error:
        error_stream.write(f"{error}\n")
        error_stream.flush()
        return 1

    output_stream.write(f"{output}\n")
    output_stream.flush()
    return 0


def _parse_thinking(value: str | None) -> ThinkingSetting | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    return value


if __name__ == "__main__":
    raise SystemExit(main())
