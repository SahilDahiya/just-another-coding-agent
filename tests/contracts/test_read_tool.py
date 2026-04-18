from __future__ import annotations

import pytest

from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    DangerFullAccessSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.errors import (
    ToolEncodingError,
    ToolOperationalError,
    ToolPathError,
)
from just_another_coding_agent.tools.read import read
from tests.contracts.read_only_tool_test_support import (
    go_worker_required,
    worker_ctx,
)


@go_worker_required
async def test_read_tool_reads_utf8_text_file(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "note.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")

    try:
        result = await read(ctx, "note.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "hello\nworld\n"


@go_worker_required
async def test_read_tool_reads_requested_line_window(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "note.txt"
    path.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    try:
        result = await read(ctx, "note.txt", offset=2, limit=2)
    finally:
        await ctx.deps.read_only_worker.close()

    assert (
        result.return_value
        == "line2\nline3\n\n[1 more lines in file. Use offset=4 to continue.]"
    )


@go_worker_required
async def test_read_tool_truncates_large_file_and_returns_continuation_hint(
    tmp_path,
) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "large.txt"
    path.write_text(
        "".join(f"line {number}\n" for number in range(1, 2105)),
        encoding="utf-8",
    )

    try:
        result = await read(ctx, "large.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    text = result.return_value
    assert text.startswith("line 1\nline 2\n")
    assert "\nline 2000\n" in text
    assert "\nline 2001\n" not in text
    assert text.endswith(
        "\n\n[Showing lines 1-2000 of 2104. Use offset=2001 to continue.]"
    )


@go_worker_required
async def test_read_tool_fails_for_missing_file(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)

    try:
        with pytest.raises(ToolPathError):
            await read(ctx, "missing.txt")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_read_tool_fails_for_directory(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    (ctx.deps.workspace_root / "nested").mkdir()

    try:
        with pytest.raises(ToolPathError):
            await read(ctx, "nested")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_read_tool_fails_for_invalid_utf8(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    try:
        with pytest.raises(ToolEncodingError):
            await read(ctx, "binary.bin")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_read_tool_fails_when_offset_is_beyond_end_of_file(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "note.txt"
    path.write_text("line1\nline2\n", encoding="utf-8")

    try:
        with pytest.raises(
            ToolOperationalError,
            match="offset 5 is beyond end of file \\(2 lines total\\)",
        ):
            await read(ctx, "note.txt", offset=5)
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_read_tool_requires_approval_for_outside_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    ctx = worker_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    try:
        with pytest.raises(
            RuntimeError,
            match="Read requires approval, but no approval requester is configured",
        ):
            await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()


@go_worker_required
async def test_read_tool_allows_outside_workspace_paths_in_full_access_mode(
    tmp_path,
) -> None:
    ctx = worker_ctx(
        tmp_path,
        permission_state=build_permission_state(
            sandbox_policy=DangerFullAccessSandboxPolicy(network_access="enabled"),
            approval_policy=ApprovalPolicy(mode="never"),
        ),
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    try:
        result = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "secret"


@go_worker_required
async def test_read_tool_requests_approval_for_outside_workspace_path_in_default_mode(
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
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    try:
        result = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "secret"
    assert len(requests) == 1
    assert requests[0].reason == "allow read outside workspace: ../outside.txt"
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside.resolve()),
    )
