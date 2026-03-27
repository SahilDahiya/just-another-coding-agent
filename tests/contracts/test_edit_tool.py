import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts.tools import EditToolInput
from just_another_coding_agent.tools.edit import execute_edit


def test_edit_tool_replaces_exact_unique_text(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    result = execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text="world",
            new_text="agent",
        ),
        workspace_root=workspace_root,
    )

    assert path.read_bytes() == b"hello\nagent\n"
    assert result == f"Edited {path}"


def test_edit_tool_allows_deleting_text(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text="world\n",
            new_text="",
        ),
        workspace_root=workspace_root,
    )

    assert path.read_bytes() == b"hello\n"


def test_edit_tool_rejects_non_string_input() -> None:
    with pytest.raises(ValidationError):
        EditToolInput(path=123, old_text="old", new_text="new")


def test_edit_tool_rejects_empty_old_text() -> None:
    with pytest.raises(ValidationError):
        EditToolInput(path="note.txt", old_text="", new_text="new")


def test_edit_tool_fails_for_missing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(FileNotFoundError):
        execute_edit(
            tool_input=EditToolInput(
                path="missing.txt",
                old_text="old",
                new_text="new",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_fails_for_directory_target(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(IsADirectoryError):
        execute_edit(
            tool_input=EditToolInput(
                path="nested",
                old_text="old",
                new_text="new",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_fails_for_invalid_utf8(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UnicodeDecodeError):
        execute_edit(
            tool_input=EditToolInput(
                path="binary.bin",
                old_text="old",
                new_text="new",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_fails_when_old_text_is_missing(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    with pytest.raises(ValueError, match="old_text must match exactly once"):
        execute_edit(
            tool_input=EditToolInput(
                path="note.txt",
                old_text="missing",
                new_text="agent",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_fails_when_old_text_is_ambiguous(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\nworld\n")

    with pytest.raises(ValueError, match="old_text must match exactly once"):
        execute_edit(
            tool_input=EditToolInput(
                path="note.txt",
                old_text="world",
                new_text="agent",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_fails_when_replacement_is_no_op(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    with pytest.raises(ValueError, match="Edit would not change file contents"):
        execute_edit(
            tool_input=EditToolInput(
                path="note.txt",
                old_text="world",
                new_text="world",
            ),
            workspace_root=workspace_root,
        )


def test_edit_tool_allows_relative_path_that_resolves_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"hello\nworld\n")

    result = execute_edit(
        tool_input=EditToolInput(
            path="../outside.txt",
            old_text="world",
            new_text="agent",
        ),
        workspace_root=workspace_root,
    )

    assert result == f"Edited {outside.resolve()}"
    assert outside.read_bytes() == b"hello\nagent\n"


def test_edit_tool_matches_old_text_without_bom_in_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes("\ufeffhello\nworld\n".encode("utf-8"))

    result = execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text="hello\nworld\n",
            new_text="hello\nagent\n",
        ),
        workspace_root=workspace_root,
    )

    assert result == f"Edited {path}"
    assert path.read_bytes() == "\ufeffhello\nagent\n".encode("utf-8")


def test_edit_tool_matches_lf_old_text_against_crlf_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\r\nworld\r\n")

    execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text="hello\nworld\n",
            new_text="hello\nagent\n",
        ),
        workspace_root=workspace_root,
    )

    assert path.read_bytes() == b"hello\r\nagent\r\n"


def test_edit_tool_falls_back_to_normalized_matching(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("say “hello”\u00a0-\u00a0world  \n", encoding="utf-8")

    execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text='say "hello" - world\n',
            new_text='say "hello" - agent\n',
        ),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == 'say "hello" - agent\n'


def test_edit_tool_fuzzy_fallback_preserves_unmatched_surrounding_content(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text(
        'keep “smart”\nchange “hello”\u00a0-\u00a0world  \n',
        encoding="utf-8",
    )

    execute_edit(
        tool_input=EditToolInput(
            path="note.txt",
            old_text='change "hello" - world\n',
            new_text='change "hello" - agent\n',
        ),
        workspace_root=workspace_root,
    )

    assert path.read_text(encoding="utf-8") == (
        'keep “smart”\nchange "hello" - agent\n'
    )
