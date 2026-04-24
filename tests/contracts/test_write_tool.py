import pytest
from types import SimpleNamespace

from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.write import execute_write, write


def test_write_tool_writes_utf8_text_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"

    result = execute_write(
        workspace_root=workspace_root,
        path="note.txt",
        content="hello\nworld\n",
    )

    assert path.read_text(encoding="utf-8") == "hello\nworld\n"
    assert result == f"Wrote {path}"


def test_write_tool_overwrites_existing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("old\n", encoding="utf-8")

    execute_write(
        workspace_root=workspace_root,
        path="note.txt",
        content="new\n",
    )

    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_tool_creates_parent_directories(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "nested" / "dir" / "note.txt"

    execute_write(
        workspace_root=workspace_root,
        path="nested/dir/note.txt",
        content="hello",
    )

    assert path.read_text(encoding="utf-8") == "hello"


def test_write_tool_allows_empty_content(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "empty.txt"

    execute_write(
        workspace_root=workspace_root,
        path="empty.txt",
        content="",
    )

    assert path.read_text(encoding="utf-8") == ""


def test_write_tool_fails_for_directory_target(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(ToolPathError):
        execute_write(
            workspace_root=workspace_root,
            path="nested",
            content="hello",
        )


def test_write_tool_allows_relative_path_that_resolves_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"

    execute_write(
        workspace_root=workspace_root,
        path="../outside.txt",
        content="hello",
    )

    assert outside.read_text(encoding="utf-8") == "hello"


async def test_write_requests_approval_for_outside_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
        )
    )

    await write(ctx, "../outside.txt", "hello")

    assert outside.read_text(encoding="utf-8") == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert requests[0].path == "../outside.txt"
    assert requests[0].change_kind == "write"
    assert requests[0].reason == (
        "allow write outside workspace: ../outside.txt "
        f"(writable roots: {outside.parent.resolve()})"
    )
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_write_roots == (
        str(outside.parent.resolve()),
    )


async def test_write_skips_approval_for_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
        )
    )

    await write(ctx, "note.txt", "hello")

    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"
    assert requests == []


async def test_write_remembers_approved_outside_root_within_one_session(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_dir = tmp_path / "pi-mono"
    outside_dir.mkdir()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
        )
    )

    await write(ctx, "../pi-mono/first.txt", "one")
    await write(ctx, "../pi-mono/second.txt", "two")

    assert (outside_dir / "first.txt").read_text(encoding="utf-8") == "one"
    assert (outside_dir / "second.txt").read_text(encoding="utf-8") == "two"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_write_roots == (
        str(outside_dir.resolve()),
    )


async def test_write_requests_policy_only_approval_for_workspace_path_in_always_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="always"),
            ),
        )
    )

    await write(ctx, "note.txt", "hello")

    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert requests[0].path == "note.txt"
    assert requests[0].change_kind == "write"
    assert requests[0].reason == "allow write: note.txt (approval policy: always)"
    assert requests[0].requested_permissions is None
    assert requests[0].requested_grants == ()
