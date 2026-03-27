import shutil

import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts.tools import GrepToolInput
from just_another_coding_agent.tools.grep import execute_grep

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")


def test_grep_tool_finds_matches_with_paths_and_line_numbers(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    alpha = workspace_root / "alpha.txt"
    beta = workspace_root / "beta.txt"
    alpha.write_text("first line\nneedle one\n", encoding="utf-8")
    beta.write_text("needle two\nother\n", encoding="utf-8")

    result = execute_grep(
        tool_input=GrepToolInput(pattern="needle"),
        workspace_root=workspace_root,
    )

    assert result == "alpha.txt:2:needle one\nbeta.txt:1:needle two"


def test_grep_tool_returns_explicit_no_match_message(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("first line\n", encoding="utf-8")

    result = execute_grep(
        tool_input=GrepToolInput(pattern="needle"),
        workspace_root=workspace_root,
    )

    assert result == "No matches found."


def test_grep_tool_fails_for_missing_search_path(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(FileNotFoundError):
        execute_grep(
            tool_input=GrepToolInput(pattern="needle", path="missing"),
            workspace_root=workspace_root,
        )


def test_grep_tool_respects_global_match_limit(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "alpha.txt"
    path.write_text("needle one\nneedle two\nneedle three\n", encoding="utf-8")

    result = execute_grep(
        tool_input=GrepToolInput(pattern="needle", limit=2),
        workspace_root=workspace_root,
    )

    assert result == (
        "alpha.txt:1:needle one\n"
        "alpha.txt:2:needle two\n\n"
        "[Showing first 2 matches. Refine pattern or path to narrow results.]"
    )


def test_grep_tool_rejects_empty_pattern() -> None:
    with pytest.raises(ValidationError):
        GrepToolInput(pattern="")
