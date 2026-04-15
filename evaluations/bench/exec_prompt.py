from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any, TextIO

from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.env import trace_mode
from just_another_coding_agent.runtime.observability import (
    configure_observability,
    export_trace_context_env,
)


class ExecPromptError(RuntimeError):
    """Raised when the one-shot wrapper cannot complete a canonical run."""


class NoRunEventsTimeout(ExecPromptError):
    """Raised when run.start produces no first observable RPC event."""


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

_PHASES_FILENAME = "exec-prompt-phases.json"
_RPC_TRANSCRIPT_FILENAME = "exec-prompt-rpc-transcript.jsonl"
_DEFAULT_FIRST_RPC_EVENT_TIMEOUT_SEC = 10.0
_EXEC_PROMPT_SPAN_NAME = "jaca.exec_prompt"
_EXEC_PROMPT_FLUSH_TIMEOUT_MILLIS = 5000


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
        "--headless",
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
    first_rpc_event_timeout_sec: float = _DEFAULT_FIRST_RPC_EVENT_TIMEOUT_SEC,
    status_stream: TextIO | None = None,
    popen_factory: Any = subprocess.Popen,
) -> str:
    resolved_workspace_root = Path(workspace_root).expanduser().resolve()
    if not resolved_workspace_root.is_dir():
        raise ExecPromptError(f"Directory does not exist: {resolved_workspace_root}")

    if sessions_root is None:
        with TemporaryDirectory(
            prefix="just-another-coding-agent-sessions."
        ) as temporary_root:
            resolved_sessions_root = Path(temporary_root)
            with _trace_exec_prompt_run(
                prompt=prompt,
                model=model,
                workspace_root=resolved_workspace_root,
                sessions_root=resolved_sessions_root,
                thinking=thinking,
            ) as trace:
                return _run_exec_prompt(
                    prompt=prompt,
                    model=model,
                    workspace_root=resolved_workspace_root,
                    thinking=thinking,
                    sessions_root=resolved_sessions_root,
                    first_rpc_event_timeout_sec=first_rpc_event_timeout_sec,
                    status_stream=status_stream,
                    popen_factory=popen_factory,
                    trace=trace,
                )

    resolved_sessions_root = Path(sessions_root).expanduser().resolve()
    with _trace_exec_prompt_run(
        prompt=prompt,
        model=model,
        workspace_root=resolved_workspace_root,
        sessions_root=resolved_sessions_root,
        thinking=thinking,
    ) as trace:
        return _run_exec_prompt(
            prompt=prompt,
            model=model,
            workspace_root=resolved_workspace_root,
            thinking=thinking,
            sessions_root=resolved_sessions_root,
            first_rpc_event_timeout_sec=first_rpc_event_timeout_sec,
            status_stream=status_stream,
            popen_factory=popen_factory,
            trace=trace,
        )


def _run_exec_prompt(
    *,
    prompt: str,
    model: str,
    workspace_root: Path,
    thinking: ThinkingSetting | None,
    sessions_root: Path,
    first_rpc_event_timeout_sec: float,
    status_stream: TextIO | None,
    popen_factory: Any,
    trace: "_ExecPromptTrace | None",
) -> str:
    diagnostics = _ExecPromptDiagnostics(root=sessions_root)
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
        env={**os.environ, **export_trace_context_env()},
    )
    diagnostics.record_phase("subprocess_started_at")
    _write_status(status_stream, "subprocess started")

    if process.stdin is None or process.stdout is None:
        raise ExecPromptError(
            "just-another-coding-agent subprocess must expose stdin and stdout"
        )

    try:
        _write_json_line(
            process.stdin,
            {"id": "req-create", "command": "session.create", "payload": {}},
            diagnostics=diagnostics,
        )
        diagnostics.record_phase("session_create_sent_at")
        session_create_response = _read_json_line(
            process.stdout,
            expected="session.create response",
            diagnostics=diagnostics,
        )
        diagnostics.record_phase("session_create_received_at")
        session_id = _extract_session_id(session_create_response)
        diagnostics.set_session_id(session_id)
        if trace is not None:
            trace.set_attribute("jaca.exec_prompt.session_id", session_id)
        _write_status(status_stream, "session created")

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
            diagnostics=diagnostics,
        )
        diagnostics.record_phase("run_start_sent_at")
        _write_status(status_stream, "run.start sent")
        diagnostics.record_phase(
            "first_rpc_event_timeout_sec",
            value=first_rpc_event_timeout_sec,
        )

        saw_first_rpc_event = False
        saw_first_tool_event = False
        saw_first_assistant_text_delta = False
        while True:
            try:
                response = _read_json_line(
                    process.stdout,
                    expected="run.start response",
                    diagnostics=diagnostics,
                    timeout_sec=(
                        first_rpc_event_timeout_sec if not saw_first_rpc_event else None
                    ),
                )
            except NoRunEventsTimeout:
                diagnostics.record_phase("no_first_rpc_event_timeout_at")
                raise
            if not saw_first_rpc_event:
                saw_first_rpc_event = True
                diagnostics.record_phase("first_rpc_event_received_at")
                _write_status(status_stream, "first rpc event received")
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
            if event_type == "assistant_text_delta":
                diagnostics.record_phase_once("first_assistant_text_delta_at")
                if not saw_first_assistant_text_delta:
                    saw_first_assistant_text_delta = True
                    _write_status(status_stream, "first assistant text delta received")
            if isinstance(event_type, str) and event_type.startswith("tool_call_"):
                diagnostics.record_phase_once("first_tool_event_at")
                if not saw_first_tool_event:
                    saw_first_tool_event = True
                    _write_status(status_stream, "first tool event received")
            if event_type == "run_succeeded":
                diagnostics.record_phase("terminal_event_at")
                diagnostics.record_phase("terminal_event_type", value="run_succeeded")
                if trace is not None:
                    trace.set_attribute(
                        "jaca.exec_prompt.terminal_event_type", "run_succeeded"
                    )
                _write_status(status_stream, "run succeeded")
                output_text = event.get("output_text")
                if not isinstance(output_text, str):
                    raise ExecPromptError(
                        "run_succeeded must include string output_text"
                    )
                return output_text

            if event_type == "run_failed":
                diagnostics.record_phase("terminal_event_at")
                diagnostics.record_phase("terminal_event_type", value="run_failed")
                if trace is not None:
                    trace.set_attribute(
                        "jaca.exec_prompt.terminal_event_type", "run_failed"
                    )
                _write_status(status_stream, "run failed")
                raise ExecPromptError(
                    f"{event.get('error_type')}: {event.get('message')}"
                )
    finally:
        process.stdin.close()
        _wait_for_process(process)


def _write_json_line(
    stream: TextIO,
    payload: dict[str, object],
    *,
    diagnostics: "_ExecPromptDiagnostics" | None = None,
) -> None:
    if diagnostics is not None:
        diagnostics.append_transcript(direction="send", payload=payload)
    stream.write(json.dumps(payload))
    stream.write("\n")
    stream.flush()


def _write_status(stream: TextIO | None, message: str) -> None:
    if stream is None:
        return
    stream.write(f"[exec_prompt] {message}\n")
    stream.flush()


def _read_json_line(
    stream: TextIO,
    *,
    expected: str,
    diagnostics: "_ExecPromptDiagnostics" | None = None,
    timeout_sec: float | None = None,
) -> dict[str, object]:
    if timeout_sec is None:
        line = stream.readline()
    else:
        line = _readline_with_timeout(
            stream,
            expected=expected,
            timeout_sec=timeout_sec,
        )
    if line == "":
        raise ExecPromptError(f"EOF while waiting for {expected}")

    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise ExecPromptError(f"Invalid JSON while waiting for {expected}") from error

    if not isinstance(payload, dict):
        raise ExecPromptError(f"{expected} must be a JSON object")
    if diagnostics is not None:
        diagnostics.append_transcript(direction="recv", payload=payload)
    return payload


def _extract_session_id(payload: dict[str, object]) -> str:
    if payload.get("type") == "rpc_error":
        raise ExecPromptError(f"{payload.get('error_type')}: {payload.get('message')}")

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


def _readline_with_timeout(
    stream: TextIO,
    *,
    expected: str,
    timeout_sec: float,
) -> str:
    queue: Queue[tuple[bool, str | BaseException]] = Queue(maxsize=1)

    def _target() -> None:
        try:
            queue.put((True, stream.readline()))
        except BaseException as error:
            queue.put((False, error))

    thread = Thread(target=_target, daemon=True)
    thread.start()
    try:
        ok, value = queue.get(timeout=timeout_sec)
    except Empty as error:
        raise NoRunEventsTimeout(
            f"No RPC event received within {timeout_sec} seconds after run.start"
        ) from error

    if ok:
        assert isinstance(value, str)
        return value

    assert isinstance(value, BaseException)
    raise value


class _ExecPromptDiagnostics:
    def __init__(self, *, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._phases_path = self._root / _PHASES_FILENAME
        self._transcript_path = self._root / _RPC_TRANSCRIPT_FILENAME
        self._payload: dict[str, object] = {}

    def set_session_id(self, session_id: str) -> None:
        self._payload["session_id"] = session_id
        self._write_phases()

    def record_phase(self, name: str, *, value: object | None = None) -> None:
        self._payload[name] = _timestamp() if value is None else value
        self._write_phases()

    def record_phase_once(self, name: str) -> None:
        if name in self._payload:
            return
        self.record_phase(name)

    def append_transcript(self, *, direction: str, payload: dict[str, object]) -> None:
        with self._transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": _timestamp(),
                        "direction": direction,
                        "payload": payload,
                    }
                )
            )
            handle.write("\n")

    def _write_phases(self) -> None:
        self._phases_path.write_text(
            json.dumps(self._payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _timestamp() -> str:
    return datetime.now(tz=UTC).isoformat()


class _ExecPromptTrace:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: object) -> None:
        self._span.set_attribute(key, value)


@contextmanager
def _trace_exec_prompt_run(
    *,
    prompt: str,
    model: str,
    workspace_root: Path,
    sessions_root: Path,
    thinking: ThinkingSetting | None,
):
    if trace_mode() != "logfire":
        yield None
        return

    try:
        configure_observability()
        logfire = importlib.import_module("logfire")
    except RuntimeError as error:
        raise ExecPromptError(str(error)) from error
    except ModuleNotFoundError as error:
        raise ExecPromptError(
            "JACA_TRACE_MODE=logfire requires the `logfire` package in this "
            "environment."
        ) from error

    metadata = {
        "jaca.exec_prompt.model": model,
        "jaca.exec_prompt.workspace_root": str(workspace_root),
        "jaca.exec_prompt.sessions_root": str(sessions_root),
        "jaca.exec_prompt.prompt_sha256": hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest(),
        "jaca.exec_prompt.prompt_preview": _build_prompt_preview(prompt),
        "jaca.exec_prompt.prompt_chars": len(prompt),
    }
    if thinking is not None:
        metadata["jaca.exec_prompt.thinking"] = str(thinking)
    for env_key in (
        "JACA_HARBOR_JOB_NAME",
        "JACA_HARBOR_SUBMISSION_ID",
        "JACA_HARBOR_SLICE_NAME",
        "TASK_NAME",
        "HARBOR_TASK_NAME",
    ):
        value = os.environ.get(env_key, "").strip()
        if value:
            metadata[f"jaca.exec_prompt.env.{env_key.lower()}"] = value

    with logfire.span(_EXEC_PROMPT_SPAN_NAME, **metadata) as span:
        trace = _ExecPromptTrace(span)
        trace.set_attribute("jaca.exec_prompt.status", "running")
        try:
            yield trace
        except Exception as error:
            trace.set_attribute("jaca.exec_prompt.status", "failed")
            trace.set_attribute("jaca.exec_prompt.error_type", type(error).__name__)
            trace.set_attribute("jaca.exec_prompt.error_message", str(error))
            raise
        else:
            trace.set_attribute("jaca.exec_prompt.status", "succeeded")
        finally:
            logfire.force_flush(timeout_millis=_EXEC_PROMPT_FLUSH_TIMEOUT_MILLIS)


def _build_prompt_preview(prompt: str, *, limit: int = 160) -> str:
    single_line = " ".join(prompt.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 3] + "..."


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
            status_stream=error_stream,
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
