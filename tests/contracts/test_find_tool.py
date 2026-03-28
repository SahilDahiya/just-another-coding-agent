import shutil

import pytest

from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.find import execute_find

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")


def test_find_tool_lists_matching_files_relative_to_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    search_root = workspace_root / "src"
    search_root.mkdir(parents=True)
    (search_root / "alpha.py").write_text("", encoding="utf-8")
    (search_root / "beta.txt").write_text("", encoding="utf-8")
    nested = search_root / "nested"
    nested.mkdir()
    (nested / "gamma.py").write_text("", encoding="utf-8")

    result = execute_find(
        workspace_root=workspace_root,
        pattern="**/*.py",
        path="src",
    )

    assert result == "alpha.py\nnested/gamma.py"


def test_find_tool_returns_explicit_no_match_message(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("", encoding="utf-8")

    result = execute_find(
        workspace_root=workspace_root,
        pattern="**/*.py",
    )

    assert result == "No files found matching pattern."


def test_find_tool_fails_for_missing_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ToolPathError):
        execute_find(
            workspace_root=workspace_root,
            pattern="*.py",
            path="missing",
        )


def test_find_tool_fails_for_non_directory_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.py").write_text("", encoding="utf-8")

    with pytest.raises(ToolPathError):
        execute_find(
            workspace_root=workspace_root,
            pattern="*.py",
            path="alpha.py",
        )


def test_find_tool_respects_result_limit(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (workspace_root / name).write_text("", encoding="utf-8")

    result = execute_find(
        workspace_root=workspace_root,
        pattern="*.py",
        limit=2,
    )

    assert result == (
        "alpha.py\nbeta.py\n\n"
        "[Showing first 2 results. Use limit=4 for more or refine the pattern.]"
    )
