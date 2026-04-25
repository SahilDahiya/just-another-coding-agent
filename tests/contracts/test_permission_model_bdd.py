from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    DangerFullAccessSandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolApprovalDenied
from just_another_coding_agent.tools.read import read
from just_another_coding_agent.tools.shell import execute_shell
from just_another_coding_agent.tools.write import write
from tests.contracts.read_only_tool_test_support import worker_ctx


@dataclass(frozen=True)
class _FakeShellContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = "call-shell"
    tool_name: str | None = "shell"


class _ExecutorHandle:
    async def read(self, _max_bytes: int) -> bytes:
        return b""

    async def wait(self) -> int:
        return 0

    async def terminate(self) -> None:
        return None

    @property
    def exit_code(self) -> int | None:
        return 0


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request):
        del request
        self.calls += 1
        return _ExecutorHandle()


class _ReadOnlyWorkerProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, _request):
        self.calls += 1
        raise AssertionError("read-only worker should not be called")


def _default_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )


def _full_access_permission_state():
    return build_permission_state(
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    )


def _always_approval_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="always"),
    )


def _never_approval_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    )


def _file_change_always_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(
            mode="on_escalation",
            by_kind={"file_change": "always"},
        ),
    )


def _file_change_never_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(
            mode="on_escalation",
            by_kind={"file_change": "never"},
        ),
    )


async def test_given_default_policy_when_workspace_read_then_allowed(tmp_path) -> None:
    ctx = worker_ctx(tmp_path)
    path = ctx.deps.workspace_root / "note.txt"
    path.write_text("hello", encoding="utf-8")

    try:
        result = await read(ctx, "note.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "hello"


async def test_given_default_policy_when_workspace_write_then_allowed(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            permission_state=_default_permission_state(),
        )
    )

    await write(ctx, "note.txt", "hello")

    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"


async def test_given_always_policy_when_workspace_read_then_permission_grant_approval_is_requested(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    note = base_ctx.deps.workspace_root / "note.txt"
    note.write_text("hello", encoding="utf-8")
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
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
            permission_state=_always_approval_permission_state(),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    try:
        result = await read(ctx, "note.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "permission_grant"
    assert requests[0].requested_permissions is None
    assert requests[0].requested_grants == ()
    assert requests[0].reason == "allow read: note.txt (approval policy: always)"


async def test_given_always_policy_when_workspace_write_then_file_change_approval_is_requested(
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
            permission_state=_always_approval_permission_state(),
        )
    )

    await write(ctx, "note.txt", "hello")

    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert requests[0].requested_permissions is None
    assert requests[0].requested_grants == ()
    assert requests[0].reason == "allow write: note.txt (approval policy: always)"


async def test_given_file_change_always_override_when_workspace_write_then_file_change_approval_is_requested(
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
            permission_state=_file_change_always_permission_state(),
        )
    )

    await write(ctx, "note.txt", "hello")

    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert requests[0].requested_permissions is None
    assert requests[0].requested_grants == ()
    assert (
        requests[0].reason
        == "allow write: note.txt (approval policy: file_change=always)"
    )


async def test_given_file_change_always_override_when_workspace_read_then_allowed_without_prompt(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    note = base_ctx.deps.workspace_root / "note.txt"
    note.write_text("hello", encoding="utf-8")
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
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
            permission_state=_file_change_always_permission_state(),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    try:
        result = await read(ctx, "note.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "hello"
    assert requests == []


async def test_given_default_policy_when_non_workspace_read_then_permission_grant_approval_is_requested(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=base_ctx.deps.workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    try:
        result = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "secret"
    assert len(requests) == 1
    assert requests[0].request_kind == "permission_grant"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_non_workspace_write_then_file_change_approval_is_requested(
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
            option_id="allow-session",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    await write(ctx, "../outside.txt", "hello")

    assert outside.read_text(encoding="utf-8") == "hello"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_shell_requests_network_then_command_execution_approval_is_requested(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
    )

    assert result == {"exit_code": 0, "output": ""}
    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "once"


async def test_given_default_policy_when_shell_requests_non_workspace_write_then_command_execution_approval_is_requested(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=f"tee {outside_dir / 'note.txt'}",
        shell_family=detect_default_shell_family(),
    )

    assert result == {"exit_code": 0, "output": ""}
    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_shell_requests_non_workspace_read_then_command_execution_approval_is_requested(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="cat ../outside.txt",
        shell_family=detect_default_shell_family(),
    )

    assert result == {"exit_code": 0, "output": ""}
    assert executor.calls == 1
    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_non_workspace_read_is_session_approved_then_second_read_does_not_prompt(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=base_ctx.deps.workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    try:
        first = await read(ctx, "../outside.txt")
        second = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert first.return_value == "secret"
    assert second.return_value == "secret"
    assert len(requests) == 1
    assert requests[0].request_kind == "permission_grant"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_non_workspace_write_is_session_approved_then_second_write_does_not_prompt(
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
            option_id="allow-session",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    await write(ctx, "../outside.txt", "first")
    await write(ctx, "../outside.txt", "second")

    assert outside.read_text(encoding="utf-8") == "second"
    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_shell_non_workspace_read_is_session_approved_then_second_read_does_not_prompt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    first = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="cat ../outside.txt",
        shell_family=detect_default_shell_family(),
    )
    second = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="cat ../outside.txt",
        shell_family=detect_default_shell_family(),
    )

    assert first == {"exit_code": 0, "output": ""}
    assert second == {"exit_code": 0, "output": ""}
    assert executor.calls == 2
    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"


async def test_given_default_policy_when_shell_network_is_approved_then_second_network_command_prompts_again(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    first = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
    )
    second = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com/status",
        shell_family=detect_default_shell_family(),
    )

    assert first == {"exit_code": 0, "output": ""}
    assert second == {"exit_code": 0, "output": ""}
    assert executor.calls == 2
    assert len(requests) == 2
    assert all(request.request_kind == "command_execution" for request in requests)
    assert all(len(request.requested_grants) == 1 for request in requests)
    assert all(request.requested_grants[0].scope == "once" for request in requests)


async def test_given_default_policy_when_shell_network_is_session_approved_then_second_network_command_does_not_prompt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        )

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    first = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
    )
    second = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com/status",
        shell_family=detect_default_shell_family(),
    )

    assert first == {"exit_code": 0, "output": ""}
    assert second == {"exit_code": 0, "output": ""}
    assert executor.calls == 2
    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "once"


async def test_given_default_policy_when_non_workspace_read_is_denied_then_read_fails_without_executing(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    worker = _ReadOnlyWorkerProbe()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="denied")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
            read_only_worker=worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval denied: allow read outside workspace: \.\./outside\.txt"
            r".*The file was not read\. Choose another approach or stop\."
        ),
    ):
        await read(ctx, "../outside.txt")

    assert len(requests) == 1
    assert requests[0].request_kind == "permission_grant"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"
    assert worker.calls == 0


async def test_given_never_policy_when_non_workspace_read_then_policy_denied_without_executing(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    worker = _ReadOnlyWorkerProbe()
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=_never_approval_permission_state(),
            read_only_worker=worker,
        ),
        tool_call_id="call-read-never",
        tool_name="read",
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval blocked by current policy: allow read outside workspace: "
            r"\.\./outside\.txt.*The file was not read\. "
            r"Choose another approach or stop\."
        ),
    ):
        await read(ctx, "../outside.txt")

    assert requests == []
    assert worker.calls == 0


async def test_given_default_policy_when_non_workspace_write_is_denied_then_write_fails_without_mutating(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="denied")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval denied: allow write outside workspace: \.\./outside\.txt"
            r".*The file was not modified\. Choose another approach or stop\."
        ),
    ):
        await write(ctx, "../outside.txt", "hello")

    assert len(requests) == 1
    assert requests[0].request_kind == "file_change"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "session"
    assert not outside.exists()


async def test_given_never_policy_when_non_workspace_write_then_policy_denied_without_mutating(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_never_approval_permission_state(),
        )
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval blocked by current policy: allow write outside workspace: "
            r"\.\./outside\.txt.*The file was not modified\. "
            r"Choose another approach or stop\."
        ),
    ):
        await write(ctx, "../outside.txt", "hello")

    assert requests == []
    assert not outside.exists()


async def test_given_default_policy_when_shell_network_is_denied_then_command_does_not_run(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="denied")

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_default_permission_state(),
        )
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval denied: allow shell command: curl https://example\.com"
            r" \(network enabled\)\. The command was not run\. "
            r"Choose another approach or stop\."
        ),
    ):
        await execute_shell(
            ctx=ctx,
            workspace_root=workspace_root,
            command="curl https://example.com",
            shell_family=detect_default_shell_family(),
        )

    assert len(requests) == 1
    assert requests[0].request_kind == "command_execution"
    assert len(requests[0].requested_grants) == 1
    assert requests[0].requested_grants[0].scope == "once"
    assert executor.calls == 0


async def test_given_never_policy_when_shell_network_is_blocked_then_command_does_not_run(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_never_approval_permission_state(),
        )
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval blocked by current policy: allow shell command: "
            r"curl https://example\.com \(network enabled\)\. "
            r"The command was not run\. Choose another approach or stop\."
        ),
    ):
        await execute_shell(
            ctx=ctx,
            workspace_root=workspace_root,
            command="curl https://example.com",
            shell_family=detect_default_shell_family(),
        )

    assert requests == []
    assert executor.calls == 0


async def test_given_file_change_never_override_when_non_workspace_write_then_policy_denied_without_mutating(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_file_change_never_permission_state(),
        )
    )

    with pytest.raises(
        ToolApprovalDenied,
        match=(
            r"Approval blocked by current policy: allow write outside workspace: "
            r"\.\./outside\.txt.*The file was not modified\. "
            r"Choose another approach or stop\."
        ),
    ):
        await write(ctx, "../outside.txt", "hello")

    assert requests == []
    assert not outside.exists()


async def test_given_full_access_when_non_workspace_read_then_allowed_without_prompt(
    tmp_path,
) -> None:
    base_ctx = worker_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=base_ctx.deps.workspace_root,
            shell_family=detect_default_shell_family(),
            approval_requester=approval_requester,
            permission_state=_full_access_permission_state(),
            read_only_worker=base_ctx.deps.read_only_worker,
        ),
        tool_call_id="call-read",
        tool_name="read",
    )

    try:
        result = await read(ctx, "../outside.txt")
    finally:
        await ctx.deps.read_only_worker.close()

    assert result.return_value == "secret"
    assert requests == []


async def test_given_full_access_when_non_workspace_write_then_allowed_without_prompt(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    requests = []

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=_full_access_permission_state(),
        )
    )

    await write(ctx, "../outside.txt", "hello")

    assert outside.read_text(encoding="utf-8") == "hello"
    assert requests == []


async def test_given_full_access_when_shell_network_then_allowed_without_prompt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executor = _Executor()

    async def approval_requester(request, _tool_call_id=None, _tool_name=None):
        requests.append(request)
        return ApprovalDecision(request_id=request.request_id, decision="approved")

    ctx = _FakeShellContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            sandbox_executor=executor,
            approval_requester=approval_requester,
            permission_state=_full_access_permission_state(),
        )
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
    )

    assert result == {"exit_code": 0, "output": ""}
    assert executor.calls == 1
    assert requests == []
