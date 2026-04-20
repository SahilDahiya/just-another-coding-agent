from __future__ import annotations

import pytest
from types import SimpleNamespace

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
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
async def test_read_requests_approval_for_outside_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=base_ctx.deps.workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-1",
        tool_name="read",
    )

    try:
        result = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "secret"
    assert len(requests) == 1
    assert requests[0].reason == (
        "allow read outside workspace: ../outside.txt "
        f"(read-only roots: {outside.parent.resolve()})"
    )
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside.parent.resolve()),
    )


@go_worker_required
async def test_read_remembers_approved_outside_root_within_one_session(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    outside_dir = tmp_path / "archive"
    outside_dir.mkdir()
    (outside_dir / "first.txt").write_text("first", encoding="utf-8")
    (outside_dir / "second.txt").write_text("second", encoding="utf-8")
    requests = []

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=base_ctx.deps.workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-1",
        tool_name="read",
    )

    try:
        first = await read(ctx, "../archive/first.txt")
        second = await read(ctx, "../archive/second.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert first.return_value == "first"
    assert second.return_value == "second"
    assert len(requests) == 1
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside_dir.resolve()),
    )
