import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts.tools import BashToolInput
from just_another_coding_agent.tools.bash import execute_bash


def test_bash_tool_runs_in_explicit_workspace_root(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = execute_bash(
        tool_input=BashToolInput(command="pwd"),
        workspace_root=workspace_root,
    )

    assert result["exit_code"] == 0
    assert result["output"].strip() == str(workspace_root)


def test_bash_tool_fails_on_non_zero_exit_and_includes_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="boom\n\nCommand exited with code 7"):
        execute_bash(
            tool_input=BashToolInput(command="printf 'boom' >&2; exit 7"),
            workspace_root=workspace_root,
        )


def test_bash_tool_returns_empty_output_when_command_prints_nothing(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = execute_bash(
        tool_input=BashToolInput(command=":"),
        workspace_root=workspace_root,
    )

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
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        TimeoutError,
        match="partial output\n\nCommand timed out after 1 seconds",
    ):
        execute_bash(
            tool_input=BashToolInput(
                command="printf 'partial output'; sleep 2",
                timeout=1,
            ),
            workspace_root=workspace_root,
        )


def test_bash_tool_truncates_large_output_and_saves_full_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = execute_bash(
        tool_input=BashToolInput(
            command=(
                "python - <<'PY'\n"
                "for i in range(1, 2105):\n"
                '    print(f"line {i}")\n'
                "PY"
            )
        ),
        workspace_root=workspace_root,
    )

    assert result["exit_code"] == 0
    output = result["output"]
    assert isinstance(output, str)
    assert "line 1\n" not in output
    assert "line 104\n" not in output
    assert "line 105\n" in output
    assert "line 2104\n" in output
    assert "[Showing lines " in output
    assert "Full output: " in output
    match = re.search(r"Full output: (?P<path>[^\]]+)\]", output)
    assert match is not None
    full_output_path = match.group("path")
    assert "line 1\n" in Path(full_output_path).read_text(encoding="utf-8")


def test_bash_tool_fails_for_invalid_utf8_output(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(UnicodeDecodeError):
        execute_bash(
            tool_input=BashToolInput(
                command="python -c \"import sys; sys.stdout.buffer.write(b'\\xff')\""
            ),
            workspace_root=workspace_root,
        )
