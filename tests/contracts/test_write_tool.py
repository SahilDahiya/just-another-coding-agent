import pytest

from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.write import execute_write


def test_write_tool_writes_utf8_text_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"

    result = execute_write(
        workspace_root=workspace_root,
        path="note.txt",
        content="hello\nworld\n",
    )

    assert path.read_text(encoding="utf-8") == "hello\nworld\n"
    assert result == f"Wrote {path}"


def test_write_tool_overwrites_existing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("old\n", encoding="utf-8")

    execute_write(
        workspace_root=workspace_root,
        path="note.txt",
        content="new\n",
    )

    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_tool_creates_parent_directories(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "nested" / "dir" / "note.txt"

    execute_write(
        workspace_root=workspace_root,
        path="nested/dir/note.txt",
        content="hello",
    )

    assert path.read_text(encoding="utf-8") == "hello"


def test_write_tool_allows_empty_content(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "empty.txt"

    execute_write(
        workspace_root=workspace_root,
        path="empty.txt",
        content="",
    )

    assert path.read_text(encoding="utf-8") == ""


def test_write_tool_fails_for_directory_target(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(ToolPathError):
        execute_write(
            workspace_root=workspace_root,
            path="nested",
            content="hello",
        )


def test_write_tool_allows_relative_path_that_resolves_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"

    execute_write(
        workspace_root=workspace_root,
        path="../outside.txt",
        content="hello",
    )

    assert outside.read_text(encoding="utf-8") == "hello"
