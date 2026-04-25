from __future__ import annotations

from typing import Protocol

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalRequest,
    SandboxPermissionGrant,
    approval_request_subject,
    normalize_approval_decision,
)
from just_another_coding_agent.contracts.tool_runtime import (
    ExecApprovalRequirement,
    ForbiddenApproval,
    NeedsApproval,
    SkipApproval,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolApprovalDenied


class ApprovalFlowContext(Protocol):
    deps: WorkspaceDeps


def remember_approved_permissions(
    *,
    permission_memory,
    permissions: AdditionalSandboxPermissions,
) -> None:
    for root in permissions.extra_read_roots:
        permission_memory.remember_read_root(root)
    for root in permissions.extra_write_roots:
        permission_memory.remember_write_root(root)


def remember_approved_grants(
    *,
    permission_memory,
    grants: tuple[SandboxPermissionGrant, ...],
) -> None:
    for grant in grants:
        if grant.scope != "session":
            continue
        if grant.command_prefix:
            permission_memory.remember_command_prefix(grant.command_prefix)
        remember_approved_permissions(
            permission_memory=permission_memory,
            permissions=grant.permissions,
        )


async def resolve_tool_approval(
    *,
    ctx: ApprovalFlowContext | None,
    request: ApprovalRequest,
    denied_message: str,
    missing_requester_message: str,
) -> ApprovalDecision:
    if ctx is None or ctx.deps.approval_requester is None:
        raise RuntimeError(missing_requester_message)

    decision = normalize_approval_decision(
        request=request,
        decision=await ctx.deps.approval_requester(
            request,
            getattr(ctx, "tool_call_id", None),
            getattr(ctx, "tool_name", None),
        ),
    )
    if decision.decision != "approved":
        raise ToolApprovalDenied(
            denied_message,
            approval_kind=request.request_kind,
            subject=approval_request_subject(request),
            retry_same_request_allowed=False,
        )

    remember_approved_grants(
        permission_memory=ctx.deps.permission_memory,
        grants=decision.granted_grants,
    )
    return decision


def deny_tool_by_policy(
    *,
    request: ApprovalRequest,
    denied_message: str,
) -> None:
    raise ToolApprovalDenied(
        denied_message,
        denial_type="policy_denied",
        approval_kind=request.request_kind,
        subject=approval_request_subject(request),
        retry_same_request_allowed=False,
    )


async def fulfill_approval_requirement(
    *,
    ctx: ApprovalFlowContext | None,
    requirement: ExecApprovalRequirement,
) -> None:
    if isinstance(requirement, SkipApproval):
        return
    if isinstance(requirement, ForbiddenApproval):
        deny_tool_by_policy(
            request=requirement.request,
            denied_message=requirement.denied_message,
        )
    if isinstance(requirement, NeedsApproval):
        await resolve_tool_approval(
            ctx=ctx,
            request=requirement.request,
            denied_message=requirement.denied_message,
            missing_requester_message=requirement.missing_requester_message,
        )
        return
    raise TypeError(
        "Unknown approval requirement variant: "
        f"{type(requirement).__name__}"
    )


__all__ = [
    "ApprovalFlowContext",
    "deny_tool_by_policy",
    "fulfill_approval_requirement",
    "remember_approved_grants",
    "remember_approved_permissions",
    "resolve_tool_approval",
]
