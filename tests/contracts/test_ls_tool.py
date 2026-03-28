import pytest

from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.ls import execute_ls


def test_ls_tool_lists_directory_entries_with_directory_suffix(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / ".hidden").write_text("", encoding="utf-8")
    (workspace_root / "alpha.txt").write_text("", encoding="utf-8")
    (workspace_root / "beta").mkdir()

    result = execute_ls(
        workspace_root=workspace_root,
    )

    assert result == ".hidden\nalpha.txt\nbeta/"


def test_ls_tool_returns_empty_directory_marker(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = execute_ls(
        workspace_root=workspace_root,
    )

    assert result == "(empty directory)"


def test_ls_tool_fails_for_missing_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ToolPathError):
        execute_ls(
            workspace_root=workspace_root,
            path="missing",
        )


def test_ls_tool_fails_for_non_directory_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("", encoding="utf-8")

    with pytest.raises(ToolPathError):
        execute_ls(
            workspace_root=workspace_root,
            path="alpha.txt",
        )


def test_ls_tool_respects_entry_limit(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for name in ("alpha.txt", "beta.txt", "gamma.txt"):
        (workspace_root / name).write_text("", encoding="utf-8")

    result = execute_ls(
        workspace_root=workspace_root,
        limit=2,
    )

    assert result == (
        "alpha.txt\nbeta.txt\n\n"
        "[Showing first 2 entries. Use limit=4 for more.]"
    )
