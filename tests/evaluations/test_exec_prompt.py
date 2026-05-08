import io
import json
import sys
import time
from pathlib import Path

import pytest

from evaluations.bench.exec_prompt import (
    BENCHMARK_WORKFLOW_PROMPT,
    ExecPromptError,
    NoRunEventsTimeout,
    build_benchmark_prompt,
    main,
    read_prompt,
    run_exec_prompt,
)


@pytest.fixture(autouse=True)
def _default_exec_prompt_trace_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "off")


class RecordingStdin:
    def __init__(self) -> None:
        self._chunks: list[str] = []
        self.closed = False

    def write(self, value: str) -> int:
        self._chunks.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def getvalue(self) -> str:
        return "".join(self._chunks)


class FakeProcess:
    def __init__(self, *, stdout_text: str, returncode: int = 0) -> None:
        self.stdin = RecordingStdin()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.wait_calls: list[int | None] = []
        self.terminated = False
        self.killed = False

    def wait(self, timeout: int | None = None) -> int:
        self.wait_calls.append(timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class BlockingStdout:
    def __init__(self, *, initial_lines: list[str], delay_seconds: float) -> None:
        self._lines = initial_lines
        self._delay_seconds = delay_seconds
        self._read_count = 0

    def readline(self) -> str:
        self._read_count += 1
        if self._read_count <= len(self._lines):
            return self._lines[self._read_count - 1]
        time.sleep(self._delay_seconds)
        return ""


class _FakeLogfireSpan:
    def __init__(
        self,
        *,
        name: str,
        attributes: dict[str, object],
        calls: list[dict[str, object]],
    ) -> None:
        self.name = name
        self.attributes = attributes
        self._calls = calls

    def __enter__(self) -> "_FakeLogfireSpan":
        self._calls.append({"type": "span_enter", "name": self.name, "span": self})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._calls.append({"type": "span_exit", "name": self.name, "span": self})
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _FakeLogfireModule:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._calls = calls

    def span(self, name: str, **attributes: object) -> _FakeLogfireSpan:
        self._calls.append({"type": "span_created", "name": name, "attrs": attributes})
        return _FakeLogfireSpan(
            name=name,
            attributes=dict(attributes),
            calls=self._calls,
        )

    def force_flush(self, *, timeout_millis: int) -> None:
        self._calls.append({"type": "force_flush", "timeout_millis": timeout_millis})


class _FakeTracerSpan:
    def __init__(
        self,
        *,
        name: str,
        attributes: dict[str, object],
        calls: list[dict[str, object]],
        current_span_stack: list[str],
    ) -> None:
        self.name = name
        self.attributes = attributes
        self._calls = calls
        self._current_span_stack = current_span_stack

    def __enter__(self) -> "_FakeTracerSpan":
        self._current_span_stack.append(self.name)
        self._calls.append({"type": "span_enter", "name": self.name, "span": self})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._calls.append({"type": "span_exit", "name": self.name, "span": self})
        popped = self._current_span_stack.pop()
        assert popped == self.name
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _FakeTracer:
    def __init__(
        self,
        *,
        calls: list[dict[str, object]],
        current_span_stack: list[str],
    ) -> None:
        self._calls = calls
        self._current_span_stack = current_span_stack

    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
    ) -> _FakeTracerSpan:
        self._calls.append({"type": "span_created", "name": name, "attrs": attributes})
        return _FakeTracerSpan(
            name=name,
            attributes=dict(attributes or {}),
            calls=self._calls,
            current_span_stack=self._current_span_stack,
        )


def _make_popen(stdout_lines: list[dict[str, object] | str], *, returncode: int = 0):
    process = FakeProcess(
        stdout_text="\n".join(
            line if isinstance(line, str) else json.dumps(line) for line in stdout_lines
        )
        + "\n",
        returncode=returncode,
    )
    captured: dict[str, object] = {}

    def factory(command: list[str], **kwargs) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    return factory, process, captured


def _workspace_trust_accept_request() -> dict[str, object]:
    return {
        "id": "req-workspace-trust-accept",
        "command": "workspace.trust_accept",
        "payload": {},
    }


def _workspace_trust_accept_response(
    *, trust_target: str = "/tmp/benchmark-workspace"
) -> dict[str, object]:
    return {
        "type": "rpc_response",
        "id": "req-workspace-trust-accept",
        "response": {
            "trusted": True,
            "trust_target": trust_target,
        },
    }


def _permission_set_request(session_id: str) -> dict[str, object]:
    return {
        "id": "req-permission-set",
        "command": "permission.set",
        "payload": {
            "session_id": session_id,
            "sandbox_policy": {
                "mode": "danger_full_access",
                "network_access": "enabled",
            },
            "approval_policy": {"mode": "never"},
        },
    }


def _permission_set_response(session_id: str) -> dict[str, object]:
    return {
        "type": "rpc_response",
        "id": "req-permission-set",
        "response": {
            "session_id": session_id,
            "permission_state": {
                "sandbox_policy": {
                    "mode": "danger_full_access",
                    "network_access": "enabled",
                },
                "approval_policy": {"mode": "never"},
                "effective_capabilities": {
                    "filesystem_access": "full_access",
                    "network_access": "enabled",
                    "execution_isolation": "unsandboxed",
                    "approval_mode": "never",
                },
            },
        },
    }


def test_run_exec_prompt_returns_terminal_output(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    output = run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.3-codex",
        workspace_root=tmp_path,
        sessions_root=sessions_root,
        popen_factory=popen_factory,
    )

    assert output == "done"
    assert captured["command"] == [
        sys.executable,
        "-m",
        "just_another_coding_agent",
        "--headless",
        "--model",
        "openai-responses:gpt-5.3-codex",
        "--workspace-root",
        str(tmp_path.resolve()),
        "--sessions-root",
        str(sessions_root.resolve()),
    ]
    requests = [
        json.loads(line)
        for line in process.stdin.getvalue().splitlines()
        if line.strip()
    ]
    assert requests == [
        _workspace_trust_accept_request(),
        {"id": "req-create", "command": "session.create", "payload": {}},
        _permission_set_request("0" * 32),
        {
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": "0" * 32,
                "prompt": build_benchmark_prompt("solve it"),
                "thinking": None,
            },
        },
    ]
    assert process.stdin.closed is True
    assert process.wait_calls == [5]

    phases_path = sessions_root / "exec-prompt-phases.json"
    transcript_path = sessions_root / "exec-prompt-rpc-transcript.jsonl"
    phases = json.loads(phases_path.read_text())
    transcript = [
        json.loads(line) for line in transcript_path.read_text().splitlines() if line
    ]

    assert phases["session_id"] == "0" * 32
    assert "subprocess_started_at" in phases
    assert "workspace_trust_accept_sent_at" in phases
    assert "workspace_trust_accept_received_at" in phases
    assert "session_create_sent_at" in phases
    assert "session_create_received_at" in phases
    assert "run_start_sent_at" in phases
    assert "first_rpc_event_received_at" in phases
    assert "terminal_event_at" in phases
    assert phases["terminal_event_type"] == "run_succeeded"
    assert transcript == [
        {
            "timestamp": transcript[0]["timestamp"],
            "direction": "send",
            "payload": _workspace_trust_accept_request(),
        },
        {
            "timestamp": transcript[1]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_response",
                "id": "req-workspace-trust-accept",
                "response": {
                    "trusted": True,
                    "trust_target": "/tmp/benchmark-workspace",
                },
            },
        },
        {
            "timestamp": transcript[2]["timestamp"],
            "direction": "send",
            "payload": {"id": "req-create", "command": "session.create", "payload": {}},
        },
        {
            "timestamp": transcript[3]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
        },
        {
            "timestamp": transcript[4]["timestamp"],
            "direction": "send",
            "payload": requests[2],
        },
        {
            "timestamp": transcript[5]["timestamp"],
            "direction": "recv",
            "payload": _permission_set_response("0" * 32),
        },
        {
            "timestamp": transcript[6]["timestamp"],
            "direction": "send",
            "payload": requests[3],
        },
        {
            "timestamp": transcript[7]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
        },
        {
            "timestamp": transcript[8]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
        },
        {
            "timestamp": transcript[9]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        },
    ]


def test_run_exec_prompt_waits_for_run_response_after_terminal_event(
    tmp_path: Path,
) -> None:
    popen_factory, process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    output = run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.4-chatgpt",
        workspace_root=tmp_path,
        sessions_root=tmp_path / "sessions",
        popen_factory=popen_factory,
    )

    assert output == "done"
    transcript_path = tmp_path / "sessions" / "exec-prompt-rpc-transcript.jsonl"
    transcript = [
        json.loads(line) for line in transcript_path.read_text().splitlines() if line
    ]
    assert transcript[-1]["payload"] == {
        "type": "rpc_response",
        "id": "req-run",
        "response": {"session_id": "0" * 32},
    }
    assert process.stdin.closed is True


def test_run_exec_prompt_emits_liveness_markers_to_status_stream(tmp_path) -> None:
    status_stream = io.StringIO()
    popen_factory, _process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "tool_call_started",
                    "tool_call_id": "call-1",
                    "tool_name": "read",
                    "args": {"path": "/tmp/example.txt"},
                    "args_valid": True,
                    "activity": {
                        "title": "read /tmp/example.txt",
                        "summary": None,
                        "duration_ms": None,
                        "details": None,
                        "group_kind": None,
                    },
                },
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "assistant_text_delta",
                    "delta": "working",
                },
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    output = run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.3-codex",
        workspace_root=tmp_path,
        sessions_root=tmp_path / "sessions",
        popen_factory=popen_factory,
        status_stream=status_stream,
    )

    assert output == "done"
    assert status_stream.getvalue().splitlines() == [
        "[exec_prompt] subprocess started",
        "[exec_prompt] workspace trust accepted",
        "[exec_prompt] session created",
        "[exec_prompt] run.start sent",
        "[exec_prompt] first rpc event received",
        "[exec_prompt] first tool event received",
        "[exec_prompt] first assistant text delta received",
        "[exec_prompt] run succeeded",
    ]


def test_run_exec_prompt_emits_logfire_task_span_and_flushes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    current_span_stack: list[str] = []
    fake_tracer = _FakeTracer(
        calls=calls,
        current_span_stack=current_span_stack,
    )
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")
    monkeypatch.setenv("JACA_HARBOR_JOB_NAME", "job-123")
    monkeypatch.setenv("JACA_HARBOR_SUBMISSION_ID", "submission-abc")
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.configure_observability",
        lambda: calls.append({"type": "configure_observability"}),
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.get_tracer",
        lambda _name: fake_tracer,
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.flush_observability",
        lambda *, timeout_millis: calls.append(
            {"type": "force_flush", "timeout_millis": timeout_millis}
        ),
    )
    popen_factory, _process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.export_trace_context_env",
        lambda: (
            {
                "JACA_TRACEPARENT": (
                    "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
                )
            }
            if current_span_stack == ["jaca.exec_prompt"]
            else pytest.fail("wrapper span was not current during trace export")
        ),
    )

    output = run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.4-chatgpt",
        workspace_root=tmp_path,
        thinking="high",
        sessions_root=tmp_path / "sessions",
        popen_factory=popen_factory,
    )

    assert output == "done"
    assert calls[0] == {"type": "configure_observability"}
    span_enter = next(call for call in calls if call["type"] == "span_enter")
    span = span_enter["span"]
    assert isinstance(span, _FakeTracerSpan)
    assert span.name == "jaca.exec_prompt"
    assert (
        span.attributes["jaca.exec_prompt.model"] == "openai-responses:gpt-5.4-chatgpt"
    )
    assert span.attributes["jaca.exec_prompt.thinking"] == "high"
    assert span.attributes["jaca.exec_prompt.prompt_preview"] == "solve it"
    assert span.attributes["jaca.exec_prompt.env.jaca_harbor_job_name"] == "job-123"
    assert (
        span.attributes["jaca.exec_prompt.env.jaca_harbor_submission_id"]
        == "submission-abc"
    )
    assert (
        span.attributes["jaca.exec_prompt.workspace_trust_target"]
        == "/tmp/benchmark-workspace"
    )
    assert span.attributes["jaca.exec_prompt.session_id"] == "0" * 32
    assert span.attributes["jaca.exec_prompt.terminal_event_type"] == "run_succeeded"
    assert span.attributes["jaca.exec_prompt.status"] == "succeeded"
    assert {"type": "force_flush", "timeout_millis": 5000} in calls


def test_run_exec_prompt_marks_logfire_task_span_failed_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    current_span_stack: list[str] = []
    fake_tracer = _FakeTracer(
        calls=calls,
        current_span_stack=current_span_stack,
    )
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.configure_observability",
        lambda: None,
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.get_tracer",
        lambda _name: fake_tracer,
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.flush_observability",
        lambda *, timeout_millis: calls.append(
            {"type": "force_flush", "timeout_millis": timeout_millis}
        ),
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.export_trace_context_env",
        lambda: (
            {
                "JACA_TRACEPARENT": (
                    "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
                )
            }
            if current_span_stack == ["jaca.exec_prompt"]
            else pytest.fail("wrapper span was not current during trace export")
        ),
    )
    popen_factory, _process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_failed",
                    "error_type": "RuntimeError",
                    "message": "boom",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    with pytest.raises(ExecPromptError, match="RuntimeError: boom"):
        run_exec_prompt(
            prompt="solve it",
            model="openai-responses:gpt-5.4-chatgpt",
            workspace_root=tmp_path,
            sessions_root=tmp_path / "sessions",
            popen_factory=popen_factory,
        )

    span_enter = next(call for call in calls if call["type"] == "span_enter")
    span = span_enter["span"]
    assert isinstance(span, _FakeTracerSpan)
    assert span.attributes["jaca.exec_prompt.status"] == "failed"
    assert span.attributes["jaca.exec_prompt.error_type"] == "ExecPromptError"
    assert span.attributes["jaca.exec_prompt.error_message"] == "RuntimeError: boom"
    assert span.attributes["jaca.exec_prompt.terminal_event_type"] == "run_failed"
    assert {"type": "force_flush", "timeout_millis": 5000} in calls


def test_run_exec_prompt_forwards_trace_context_to_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_factory, _process, captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )
    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.export_trace_context_env",
        lambda: {
            "JACA_TRACEPARENT": (
                "00-11111111111111111111111111111111-2222222222222222-01"
            ),
            "JACA_TRACESTATE": "vendor=value",
        },
    )

    output = run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.4-chatgpt",
        workspace_root=tmp_path,
        sessions_root=tmp_path / "sessions",
        popen_factory=popen_factory,
    )

    assert output == "done"
    env = captured["kwargs"]["env"]
    assert env["JACA_TRACEPARENT"] == (
        "00-11111111111111111111111111111111-2222222222222222-01"
    )
    assert env["JACA_TRACESTATE"] == "vendor=value"


def test_build_benchmark_prompt_wraps_user_prompt() -> None:
    prompt = build_benchmark_prompt("solve it")

    assert prompt.startswith(BENCHMARK_WORKFLOW_PROMPT)
    assert prompt.endswith("# Task\nsolve it")


def test_run_exec_prompt_forwards_thinking(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.3-codex",
        workspace_root=tmp_path,
        thinking="high",
        sessions_root=sessions_root,
        popen_factory=popen_factory,
    )

    requests = [
        json.loads(line)
        for line in process.stdin.getvalue().splitlines()
        if line.strip()
    ]
    assert requests[3]["payload"]["thinking"] == "high"


def test_run_exec_prompt_forwards_code_mode_flag(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.3-codex",
        workspace_root=tmp_path,
        code_mode=True,
        sessions_root=sessions_root,
        popen_factory=popen_factory,
    )

    requests = [
        json.loads(line)
        for line in process.stdin.getvalue().splitlines()
        if line.strip()
    ]
    assert requests[3]["payload"]["enable_code_mode"] is True


def test_run_exec_prompt_forwards_code_mode_only_flag(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_succeeded",
                    "output_text": "done",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    run_exec_prompt(
        prompt="solve it",
        model="openai-responses:gpt-5.3-codex",
        workspace_root=tmp_path,
        code_mode_only=True,
        sessions_root=sessions_root,
        popen_factory=popen_factory,
    )

    requests = [
        json.loads(line)
        for line in process.stdin.getvalue().splitlines()
        if line.strip()
    ]
    assert requests[3]["payload"]["enable_code_mode"] is True
    assert requests[3]["payload"]["code_mode_tools_only"] is True


def test_run_exec_prompt_raises_on_run_failed(tmp_path) -> None:
    popen_factory, _process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
            {
                "type": "rpc_event",
                "id": "req-run",
                "event": {
                    "run_id": "run-1",
                    "type": "run_failed",
                    "error_type": "RuntimeError",
                    "message": "boom",
                },
            },
            {
                "type": "rpc_response",
                "id": "req-run",
                "response": {"session_id": "0" * 32},
            },
        ]
    )

    with pytest.raises(ExecPromptError, match="RuntimeError: boom"):
        run_exec_prompt(
            prompt="solve it",
            model="openai-responses:gpt-5.3-codex",
            workspace_root=tmp_path,
            sessions_root=tmp_path / "sessions",
            popen_factory=popen_factory,
        )


def test_run_exec_prompt_raises_on_rpc_error(tmp_path) -> None:
    popen_factory, _process, _captured = _make_popen(
        [
            _workspace_trust_accept_response(),
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
            _permission_set_response("0" * 32),
            {
                "type": "rpc_error",
                "id": "req-run",
                "error_type": "UnknownSession",
                "message": "Unknown session_id: 0000",
            },
        ]
    )

    with pytest.raises(
        ExecPromptError,
        match="UnknownSession: Unknown session_id: 0000",
    ):
        run_exec_prompt(
            prompt="solve it",
            model="openai-responses:gpt-5.3-codex",
            workspace_root=tmp_path,
            sessions_root=tmp_path / "sessions",
            popen_factory=popen_factory,
        )


def test_run_exec_prompt_classifies_missing_first_rpc_event(tmp_path) -> None:
    process = FakeProcess(stdout_text="", returncode=0)
    process.stdout = BlockingStdout(
        initial_lines=[
            json.dumps(_workspace_trust_accept_response()) + "\n",
            json.dumps(
                {
                    "type": "rpc_response",
                    "id": "req-create",
                    "response": {"session_id": "0" * 32},
                }
            )
            + "\n",
            json.dumps(_permission_set_response("0" * 32)) + "\n",
        ],
        delay_seconds=1.0,
    )
    captured: dict[str, object] = {}

    def popen_factory(command: list[str], **kwargs) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    sessions_root = tmp_path / "sessions"

    with pytest.raises(
        NoRunEventsTimeout,
        match="No RPC event received within 0.01 seconds after run.start",
    ):
        run_exec_prompt(
            prompt="solve it",
            model="ollama:glm-5:cloud",
            workspace_root=tmp_path,
            sessions_root=sessions_root,
            first_rpc_event_timeout_sec=0.01,
            popen_factory=popen_factory,
        )

    phases = json.loads((sessions_root / "exec-prompt-phases.json").read_text())
    assert phases["first_rpc_event_timeout_sec"] == 0.01
    assert "no_first_rpc_event_timeout_at" in phases
    transcript = [
        json.loads(line)
        for line in (sessions_root / "exec-prompt-rpc-transcript.jsonl")
        .read_text()
        .splitlines()
        if line
    ]
    assert [entry["direction"] for entry in transcript] == [
        "send",
        "recv",
        "send",
        "recv",
        "send",
        "recv",
        "send",
    ]


def test_run_exec_prompt_raises_on_workspace_trust_error(tmp_path: Path) -> None:
    popen_factory, _process, _captured = _make_popen(
        [
            {
                "type": "rpc_error",
                "id": "req-workspace-trust-accept",
                "error_type": "WorkspaceUntrusted",
                "message": "benchmark trust bootstrap failed",
            }
        ]
    )

    with pytest.raises(
        ExecPromptError,
        match="WorkspaceUntrusted: benchmark trust bootstrap failed",
    ):
        run_exec_prompt(
            prompt="solve it",
            model="openai-responses:gpt-5.4-chatgpt",
            workspace_root=tmp_path,
            sessions_root=tmp_path / "sessions",
            popen_factory=popen_factory,
        )


def test_read_prompt_reads_stdin_when_argument_missing() -> None:
    stdin = io.StringIO("hello from stdin")
    assert read_prompt(None, stdin=stdin) == "hello from stdin"


def test_main_prints_output_and_returns_zero(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fake_run_exec_prompt(**_kwargs) -> str:
        return "done"

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "done\n"
    assert stderr.getvalue() == ""


def test_main_prints_error_and_returns_one(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fake_run_exec_prompt(**_kwargs) -> str:
        raise ExecPromptError("RuntimeError: boom")

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "RuntimeError: boom\n"


def test_main_parses_thinking_flag(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    captured: dict[str, object] = {}

    def fake_run_exec_prompt(**kwargs) -> str:
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "--thinking",
            "high",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert captured["thinking"] == "high"


def test_main_parses_code_mode_flag(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    captured: dict[str, object] = {}

    def fake_run_exec_prompt(**kwargs) -> str:
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "--code-mode",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert captured["code_mode"] is True


def test_main_parses_code_mode_only_flag(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    captured: dict[str, object] = {}

    def fake_run_exec_prompt(**kwargs) -> str:
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "--code-mode-only",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert captured["code_mode_only"] is True


def test_main_passes_stderr_as_status_stream(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    captured: dict[str, object] = {}

    def fake_run_exec_prompt(**kwargs) -> str:
        captured.update(kwargs)
        status_stream = kwargs["status_stream"]
        status_stream.write("[exec_prompt] subprocess started\n")
        status_stream.flush()
        return "done"

    monkeypatch.setattr(
        "evaluations.bench.exec_prompt.run_exec_prompt",
        fake_run_exec_prompt,
    )

    exit_code = main(
        [
            "--model",
            "openai-responses:gpt-5.3-codex",
            "-C",
            str(tmp_path),
            "solve it",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert captured["status_stream"] is stderr
    assert stdout.getvalue() == "done\n"
    assert stderr.getvalue() == "[exec_prompt] subprocess started\n"
