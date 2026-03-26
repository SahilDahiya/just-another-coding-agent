import pytest
from pydantic import ValidationError

from pi_code_agent.contracts.tools import ReadToolInput
from pi_code_agent.tools.read import execute_read


def test_read_tool_reads_utf8_text_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")

    result = execute_read(
        tool_input=ReadToolInput(path="note.txt"),
        workspace_root=workspace_root,
    )

    assert result == "hello\nworld\n"


def test_read_tool_rejects_non_string_input() -> None:
    with pytest.raises(ValidationError):
        ReadToolInput(path=123)


def test_read_tool_fails_for_missing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(FileNotFoundError):
        execute_read(
            tool_input=ReadToolInput(path="missing.txt"),
            workspace_root=workspace_root,
        )


def test_read_tool_fails_for_directory(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(IsADirectoryError):
        execute_read(
            tool_input=ReadToolInput(path="nested"),
            workspace_root=workspace_root,
        )


def test_read_tool_fails_for_invalid_utf8(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UnicodeDecodeError):
        execute_read(
            tool_input=ReadToolInput(path="binary.bin"),
            workspace_root=workspace_root,
        )


def test_read_tool_fails_when_path_escapes_workspace_root(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="Path escapes workspace root"):
        execute_read(
            tool_input=ReadToolInput(path="../outside.txt"),
            workspace_root=workspace_root,
        )
