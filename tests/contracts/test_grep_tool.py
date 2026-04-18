from __future__ import annotations

import shutil

import pytest

from just_another_coding_agent.contracts.sandbox import ApprovalDecision
from just_another_coding_agent.tools import grep as grep_module
from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.grep import grep
from tests.contracts.read_only_tool_test_support import (
    go_worker_required,
    worker_ctx,
)

pytestmark = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="rg required",
)


@go_worker_required
async def test_grep_tool_finds_matches_with_paths_and_line_numbers(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    alpha = ctx.deps.workspace_root / "alpha.txt"
    beta = ctx.deps.workspace_root / "beta.txt"
    alpha.write_text("first line\nneedle one\n", encoding="utf-8")
    beta.write_text("needle two\nother\n", encoding="utf-8")

    try:
        result = await grep(ctx, "needle")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "alpha.txt:2:needle one\nbeta.txt:1:needle two"


@go_worker_required
async def test_grep_tool_returns_explicit_no_match_message(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / "alpha.txt").write_text("first line\n", encoding="utf-8")

    try:
        result = await grep(ctx, "needle")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "No matches found."


@go_worker_required
async def test_grep_tool_fails_for_missing_search_path(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)

    try:
        with pytest.raises(ToolPathError):
            await grep(ctx, "needle", path="missing")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_grep_tool_respects_global_match_limit(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "alpha.txt"
    path.write_text("needle one\nneedle two\nneedle three\n", encoding="utf-8")

    try:
        result = await grep(ctx, "needle", limit=2)
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == (
        "alpha.txt:1:needle one\n"
        "alpha.txt:2:needle two\n\n"
        "[Showing first 2 matches. Refine pattern or path to narrow results.]"
    )


async def test_grep_tool_bootstrap_failure_is_fatal(tmp_path, monkeypatch) -> None:
    ctx = worker_ctx(tmp_path)
    monkeypatch.setattr(grep_module.os, "name", "nt")
    monkeypatch.setattr(
        grep_module,
        "ensure_windows_search_tool",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap failed")),
    )

    try:
        with pytest.raises(RuntimeError, match="bootstrap failed"):
            await grep(ctx, "needle")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_grep_tool_requests_approval_for_outside_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    requests = []

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = worker_ctx(tmp_path, approval_requester=approval_requester)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "alpha.txt").write_text("needle one\n", encoding="utf-8")

    try:
        result = await grep(ctx, "needle", path="../outside")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "../outside/alpha.txt:1:needle one"
    assert len(requests) == 1
    assert requests[0].reason == "allow grep outside workspace: ../outside"
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside.resolve()),
    )
