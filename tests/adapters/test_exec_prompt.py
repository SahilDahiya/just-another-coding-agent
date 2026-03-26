import io
import json
import sys

import pytest

from pi_code_agent_adapters.bench.exec_prompt import (
    ExecPromptError,
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
        "pi_code_agent",
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
                "prompt": "solve it",
            },
        },
    ]
    assert process.stdin.closed is True
    assert process.wait_calls == [5]


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


def test_read_prompt_reads_stdin_when_argument_missing() -> None:
    stdin = io.StringIO("hello from stdin")
    assert read_prompt(None, stdin=stdin) == "hello from stdin"


def test_main_prints_output_and_returns_zero(tmp_path, monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fake_run_exec_prompt(**_kwargs) -> str:
        return "done"

    monkeypatch.setattr(
        "pi_code_agent_adapters.bench.exec_prompt.run_exec_prompt",
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
        "pi_code_agent_adapters.bench.exec_prompt.run_exec_prompt",
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
