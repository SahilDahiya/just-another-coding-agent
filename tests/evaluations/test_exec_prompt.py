import io
import json
import sys
import time

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
    def __init__(self, *, first_line: str, delay_seconds: float) -> None:
        self._lines = [first_line]
        self._delay_seconds = delay_seconds
        self._read_count = 0

    def readline(self) -> str:
        self._read_count += 1
        if self._read_count == 1:
            return self._lines[0]
        time.sleep(self._delay_seconds)
        return ""


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


def test_run_exec_prompt_returns_terminal_output(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, captured = _make_popen(
        [
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
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
        {"id": "req-create", "command": "session.create", "payload": {}},
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
            "payload": {"id": "req-create", "command": "session.create", "payload": {}},
        },
        {
            "timestamp": transcript[1]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
        },
        {
            "timestamp": transcript[2]["timestamp"],
            "direction": "send",
            "payload": requests[1],
        },
        {
            "timestamp": transcript[3]["timestamp"],
            "direction": "recv",
            "payload": {
                "type": "rpc_event",
                "id": "req-run",
                "event": {"run_id": "run-1", "type": "run_started"},
            },
        },
        {
            "timestamp": transcript[4]["timestamp"],
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
    ]


def test_run_exec_prompt_emits_liveness_markers_to_status_stream(tmp_path) -> None:
    status_stream = io.StringIO()
    popen_factory, _process, _captured = _make_popen(
        [
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
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
        "[exec_prompt] session created",
        "[exec_prompt] run.start sent",
        "[exec_prompt] first rpc event received",
        "[exec_prompt] first tool event received",
        "[exec_prompt] first assistant text delta received",
        "[exec_prompt] run succeeded",
    ]


def test_build_benchmark_prompt_wraps_user_prompt() -> None:
    prompt = build_benchmark_prompt("solve it")

    assert prompt.startswith(BENCHMARK_WORKFLOW_PROMPT)
    assert prompt.endswith("# Task\nsolve it")


def test_run_exec_prompt_forwards_thinking(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    popen_factory, process, _captured = _make_popen(
        [
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
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
    assert requests[1]["payload"]["thinking"] == "high"


def test_run_exec_prompt_raises_on_run_failed(tmp_path) -> None:
    popen_factory, _process, _captured = _make_popen(
        [
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
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
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            },
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
        first_line=json.dumps(
            {
                "type": "rpc_response",
                "id": "req-create",
                "response": {"session_id": "0" * 32},
            }
        )
        + "\n",
        delay_seconds=0.05,
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
    assert [entry["direction"] for entry in transcript] == ["send", "recv", "send"]


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
