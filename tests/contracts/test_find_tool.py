import shutil

import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts.tools import FindToolInput
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
        tool_input=FindToolInput(pattern="**/*.py", path="src"),
        workspace_root=workspace_root,
    )

    assert result == "alpha.py\nnested/gamma.py"


def test_find_tool_returns_explicit_no_match_message(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("", encoding="utf-8")

    result = execute_find(
        tool_input=FindToolInput(pattern="**/*.py"),
        workspace_root=workspace_root,
    )

    assert result == "No files found matching pattern."


def test_find_tool_fails_for_missing_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(FileNotFoundError):
        execute_find(
            tool_input=FindToolInput(pattern="*.py", path="missing"),
            workspace_root=workspace_root,
        )


def test_find_tool_fails_for_non_directory_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.py").write_text("", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        execute_find(
            tool_input=FindToolInput(pattern="*.py", path="alpha.py"),
            workspace_root=workspace_root,
        )


def test_find_tool_respects_result_limit(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (workspace_root / name).write_text("", encoding="utf-8")

    result = execute_find(
        tool_input=FindToolInput(pattern="*.py", limit=2),
        workspace_root=workspace_root,
    )

    assert result == (
        "alpha.py\nbeta.py\n\n"
        "[Showing first 2 results. Use limit=4 for more or refine the pattern.]"
    )


def test_find_tool_rejects_empty_pattern() -> None:
    with pytest.raises(ValidationError):
        FindToolInput(pattern="")
