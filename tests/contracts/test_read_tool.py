import pytest

from just_another_coding_agent.tools.errors import (
    ToolEncodingError,
    ToolOperationalError,
    ToolPathError,
)
from just_another_coding_agent.tools.read import execute_read


def test_read_tool_reads_utf8_text_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")

    result = execute_read(
        workspace_root=workspace_root,
        path="note.txt",
    )

    assert result == "hello\nworld\n"


def test_read_tool_reads_requested_line_window(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = execute_read(
        workspace_root=workspace_root,
        path="note.txt",
        offset=2,
        limit=2,
    )

    assert result == "line2\nline3\n\n[1 more lines in file. Use offset=4 to continue.]"


def test_read_tool_truncates_large_file_and_returns_continuation_hint(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "large.txt"
    path.write_text(
        "".join(f"line {number}\n" for number in range(1, 2105)),
        encoding="utf-8",
    )

    result = execute_read(
        workspace_root=workspace_root,
        path="large.txt",
    )

    assert result.startswith("line 1\nline 2\n")
    assert "\nline 2000\n" in result
    assert "\nline 2001\n" not in result
    assert result.endswith(
        "\n\n[Showing lines 1-2000 of 2104. Use offset=2001 to continue.]"
    )


def test_read_tool_fails_for_missing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ToolPathError):
        execute_read(
            workspace_root=workspace_root,
            path="missing.txt",
        )


def test_read_tool_fails_for_directory(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(ToolPathError):
        execute_read(
            workspace_root=workspace_root,
            path="nested",
        )


def test_read_tool_fails_for_invalid_utf8(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ToolEncodingError):
        execute_read(
            workspace_root=workspace_root,
            path="binary.bin",
        )


def test_read_tool_fails_when_offset_is_beyond_end_of_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("line1\nline2\n", encoding="utf-8")

    with pytest.raises(
        ToolOperationalError,
        match="Offset 5 is beyond end of file \\(2 lines total\\)",
    ):
        execute_read(
            workspace_root=workspace_root,
            path="note.txt",
            offset=5,
        )


def test_read_tool_allows_relative_path_that_resolves_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = execute_read(
        workspace_root=workspace_root,
        path="../outside.txt",
    )

    assert result == "secret"
