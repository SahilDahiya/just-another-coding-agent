import pytest

from pi_code_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)


def test_normalize_workspace_root_fails_when_missing(tmp_path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(FileNotFoundError, match="Workspace root does not exist"):
        normalize_workspace_root(missing)


def test_normalize_workspace_root_fails_when_not_directory(tmp_path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="Workspace root is not a directory"):
        normalize_workspace_root(file_path)


def test_resolve_workspace_path_accepts_absolute_path_inside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    note = workspace_root / "note.txt"
    note.write_text("hello", encoding="utf-8")

    resolved = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=str(note),
    )

    assert resolved == note.resolve()


def test_resolve_workspace_path_rejects_symlink_that_points_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace_root / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="Path escapes workspace root"):
        resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path="link.txt",
        )
