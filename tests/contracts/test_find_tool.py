from __future__ import annotations

import shutil

import pytest

from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.find import find
from tests.contracts.read_only_tool_test_support import (
    go_worker_required,
    worker_ctx,
)

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")


@go_worker_required
async def test_find_tool_lists_matching_files_relative_to_search_path(
    tmp_path,
) -> None:
    ctx = worker_ctx(tmp_path)
    search_root = ctx.deps.workspace_root / "src"
    search_root.mkdir(parents=True)
    (search_root / "alpha.py").write_text("", encoding="utf-8")
    (search_root / "beta.txt").write_text("", encoding="utf-8")
    nested = search_root / "nested"
    nested.mkdir()
    (nested / "gamma.py").write_text("", encoding="utf-8")

    try:
        result = await find(ctx, "**/*.py", path="src")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "alpha.py\nnested/gamma.py"


@go_worker_required
async def test_find_tool_returns_explicit_no_match_message(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / "alpha.txt").write_text("", encoding="utf-8")

    try:
        result = await find(ctx, "**/*.py")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "No files found matching pattern."


@go_worker_required
async def test_find_tool_fails_for_missing_search_path(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)

    try:
        with pytest.raises(ToolPathError):
            await find(ctx, "*.py", path="missing")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_find_tool_fails_for_non_directory_search_path(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / "alpha.py").write_text("", encoding="utf-8")

    try:
        with pytest.raises(ToolPathError):
            await find(ctx, "*.py", path="alpha.py")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_find_tool_respects_result_limit(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (ctx.deps.workspace_root / name).write_text("", encoding="utf-8")

    try:
        result = await find(ctx, "*.py", limit=2)
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == (
        "alpha.py\nbeta.py\n\n"
        "[Showing first 2 results. Use limit=4 for more or refine the pattern.]"
    )
