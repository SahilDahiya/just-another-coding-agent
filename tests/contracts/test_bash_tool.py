import pytest
from pydantic import ValidationError

from pi_code_agent.contracts.tools import BashToolInput
from pi_code_agent.tools.bash import execute_bash


def test_bash_tool_runs_command_and_returns_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = execute_bash(BashToolInput(command="pwd"))

    assert result["exit_code"] == 0
    assert result["output"].strip() == str(tmp_path)


def test_bash_tool_preserves_non_zero_exit_code(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = execute_bash(
        BashToolInput(command="printf 'boom' >&2; exit 7")
    )

    assert result == {"exit_code": 7, "output": "boom"}


def test_bash_tool_returns_empty_output_when_command_prints_nothing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = execute_bash(BashToolInput(command=":"))

    assert result == {"exit_code": 0, "output": ""}


def test_bash_tool_rejects_empty_command() -> None:
    with pytest.raises(ValidationError):
        BashToolInput(command="")


def test_bash_tool_rejects_non_string_command() -> None:
    with pytest.raises(ValidationError):
        BashToolInput(command=123)


def test_bash_tool_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        BashToolInput(command="pwd", timeout=0)


def test_bash_tool_fails_on_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        TimeoutError,
        match="Bash command timed out after 1 seconds",
    ):
        execute_bash(BashToolInput(command="sleep 2", timeout=1))


def test_bash_tool_fails_for_invalid_utf8_output(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(UnicodeDecodeError):
        execute_bash(
            BashToolInput(
                command="python -c \"import sys; sys.stdout.buffer.write(b'\\xff')\""
            )
        )
