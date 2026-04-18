from __future__ import annotations

import pytest

from just_another_coding_agent.contracts.sandbox import ApprovalDecision
from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.ls import ls
from tests.contracts.read_only_tool_test_support import (
    go_worker_required,
    worker_ctx,
)


@go_worker_required
async def test_ls_tool_lists_directory_entries_with_directory_suffix(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / ".hidden").write_text("", encoding="utf-8")
    (ctx.deps.workspace_root / "alpha.txt").write_text("", encoding="utf-8")
    (ctx.deps.workspace_root / "beta").mkdir()

    try:
        result = await ls(ctx)
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == ".hidden\nalpha.txt\nbeta/"


@go_worker_required
async def test_ls_tool_returns_empty_directory_marker(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)

    try:
        result = await ls(ctx)
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "(empty directory)"


@go_worker_required
async def test_ls_tool_fails_for_missing_path(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)

    try:
        with pytest.raises(ToolPathError):
            await ls(ctx, path="missing")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_ls_tool_fails_for_non_directory_path(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / "alpha.txt").write_text("", encoding="utf-8")

    try:
        with pytest.raises(ToolPathError):
            await ls(ctx, path="alpha.txt")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_ls_tool_respects_entry_limit(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    for name in ("alpha.txt", "beta.txt", "gamma.txt"):
        (ctx.deps.workspace_root / name).write_text("", encoding="utf-8")

    try:
        result = await ls(ctx, limit=2)
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == (
        "alpha.txt\nbeta.txt\n\n[Showing first 2 entries. Use limit=4 for more.]"
    )


@go_worker_required
async def test_ls_tool_requests_approval_for_outside_workspace_path_in_default_mode(
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
    (outside / "alpha.txt").write_text("", encoding="utf-8")

    try:
        result = await ls(ctx, path="../outside")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "alpha.txt"
    assert len(requests) == 1
    assert requests[0].reason == "allow ls outside workspace: ../outside"
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside.resolve()),
    )
