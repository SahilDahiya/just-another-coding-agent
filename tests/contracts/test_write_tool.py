import pytest
from pydantic import ValidationError

from pi_code_agent.contracts.tools import WriteToolInput
from pi_code_agent.tools.write import execute_write


def test_write_tool_writes_utf8_text_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"

    result = execute_write(
        tool_input=WriteToolInput(path="note.txt", content="hello\nworld\n"),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == "hello\nworld\n"
    assert result == f"Wrote {path}"


def test_write_tool_overwrites_existing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("old\n", encoding="utf-8")

    execute_write(
        tool_input=WriteToolInput(path="note.txt", content="new\n"),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_tool_creates_parent_directories(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "nested" / "dir" / "note.txt"

    execute_write(
        tool_input=WriteToolInput(path="nested/dir/note.txt", content="hello"),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == "hello"


def test_write_tool_allows_empty_content(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "empty.txt"

    execute_write(
        tool_input=WriteToolInput(path="empty.txt", content=""),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == ""


def test_write_tool_rejects_non_string_path() -> None:
    with pytest.raises(ValidationError):
        WriteToolInput(path=123, content="hello")


def test_write_tool_rejects_non_string_content() -> None:
    with pytest.raises(ValidationError):
        WriteToolInput(path="note.txt", content=123)


def test_write_tool_fails_for_directory_target(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(IsADirectoryError):
        execute_write(
            tool_input=WriteToolInput(path="nested", content="hello"),
            workspace_root=workspace_root,
        )


def test_write_tool_fails_when_path_escapes_workspace_root(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ValueError, match="Path escapes workspace root"):
        execute_write(
            tool_input=WriteToolInput(path="../outside.txt", content="hello"),
            workspace_root=workspace_root,
        )
