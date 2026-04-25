from __future__ import annotations

from dataclasses import dataclass

from just_another_coding_agent.contracts import tool_runtime as runtime_contracts
from just_another_coding_agent.contracts.sandbox import (
    CommandExecutionApprovalRequest,
    EffectiveCapabilities,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    NormalizedSandboxPolicy,
)
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan


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

        async def run(self, req: str, ctx: object) -> str:
            del ctx
            return req.upper()

    runtime = _Runtime()

    assert isinstance(runtime, runtime_contracts.Approvable)
    assert isinstance(runtime, runtime_contracts.Sandboxable)
    assert isinstance(runtime, runtime_contracts.ToolRuntime)
