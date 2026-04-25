from __future__ import annotations

from dataclasses import dataclass

import pytest

from just_another_coding_agent.contracts import tool_runtime as runtime_contracts
from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    CommandExecutionApprovalRequest,
    EffectiveCapabilities,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    NormalizedSandboxPolicy,
)
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan
from just_another_coding_agent.tools._permissions import build_file_access_runtime
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.shell import build_shell_tool_runtime


def _capabilities() -> EffectiveCapabilities:
    return EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
        approval_by_kind={},
    )


def _sandbox_plan() -> SandboxExecutionPlan:
    return SandboxExecutionPlan(
        requested_permissions=None,
        requested_capabilities=_capabilities(),
        normalized_policy=NormalizedSandboxPolicy(
            filesystem=FileSystemSandboxPolicy(access="workspace_write"),
            network=NetworkSandboxPolicy(access="restricted"),
            execution_isolation="sandboxed",
        ),
        approval_disposition="allowed",
    )


def _approval_request() -> CommandExecutionApprovalRequest:
    return CommandExecutionApprovalRequest(
        request_id="approval-1",
        request_kind="command_execution",
        reason="allow shell command: printf ok",
        command="printf ok",
        cwd="/workspace",
        shell_family="posix",
        requested_capabilities=_capabilities(),
    )


def _hello_command(shell_family: str) -> str:
    if shell_family == "powershell":
        return "[Console]::Out.Write('hello')"
    return "printf hello"


def test_tool_runtime_contract_exports_expected_types() -> None:
    assert set(runtime_contracts.__all__) == {
        "Approvable",
        "ExecApprovalRequirement",
        "ForbiddenApproval",
        "NeedsApproval",
        "Sandboxable",
        "SkipApproval",
        "ToolRuntime",
    }


def test_exec_approval_requirement_variants_carry_expected_data() -> None:
    request = _approval_request()

    assert runtime_contracts.SkipApproval().kind == "skip"
    assert runtime_contracts.NeedsApproval(
        request=request,
        denied_message="Approval denied",
        missing_requester_message="Requester missing",
    ) == runtime_contracts.NeedsApproval(
        request=request,
        denied_message="Approval denied",
        missing_requester_message="Requester missing",
    )
    assert runtime_contracts.ForbiddenApproval(
        request=request,
        denied_message="Approval blocked by current policy",
    ) == runtime_contracts.ForbiddenApproval(
        request=request,
        denied_message="Approval blocked by current policy",
    )


def test_tool_runtime_protocols_are_runtime_checkable() -> None:
    @dataclass(frozen=True)
    class _Runtime:
        sandbox_plan: SandboxExecutionPlan = _sandbox_plan()

        def approval_requirement(self) -> runtime_contracts.ExecApprovalRequirement:
            return runtime_contracts.SkipApproval()

        async def run(self, ctx: str) -> str:
            del ctx
            return "OK"

    runtime = _Runtime()

    assert isinstance(runtime, runtime_contracts.Approvable)
    assert isinstance(runtime, runtime_contracts.Sandboxable)
    assert isinstance(runtime, runtime_contracts.ToolRuntime)


@pytest.mark.parametrize(
    ("action", "access_kind", "tool_path", "expected_request_kind"),
    [
        ("read", "read", "../outside.txt", "permission_grant"),
        ("write", "write", "../outside.txt", "file_change"),
    ],
)
async def test_file_access_runtime_is_concrete_tool_runtime(
    tmp_path,
    action: str,
    access_kind: str,
    tool_path: str,
    expected_request_kind: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    runtime = build_file_access_runtime(
        permission_state=deps.permission_state,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        workspace_root=workspace_root,
        permission_memory=deps.permission_memory,
    )

    assert isinstance(runtime, runtime_contracts.Approvable)
    assert isinstance(runtime, runtime_contracts.Sandboxable)
    assert isinstance(runtime, runtime_contracts.ToolRuntime)
    requirement = runtime.approval_requirement()
    assert isinstance(requirement, runtime_contracts.NeedsApproval)
    assert requirement.request.request_kind == expected_request_kind

    plan = await runtime.run(None)

    assert plan == runtime.file_access_plan
    assert plan.sandbox_plan == runtime.sandbox_plan


async def test_shell_tool_runtime_is_concrete_tool_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    shell_family = detect_default_shell_family()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    runtime = build_shell_tool_runtime(
        deps=deps,
        workspace_root=workspace_root,
        command=_hello_command(shell_family),
        shell_family=shell_family,
    )

    assert isinstance(runtime, runtime_contracts.Approvable)
    assert isinstance(runtime, runtime_contracts.Sandboxable)
    assert isinstance(runtime, runtime_contracts.ToolRuntime)
    assert isinstance(runtime.approval_requirement(), runtime_contracts.SkipApproval)

    result = await runtime.run(None)

    assert result == {"exit_code": 0, "output": "hello"}
