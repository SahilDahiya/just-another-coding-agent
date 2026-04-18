from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalRequest,
    derive_requested_capabilities,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools._workspace import (
    path_is_within_workspace,
    resolve_workspace_path,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps


class ToolExecutionContext(Protocol):
    deps: WorkspaceDeps


async def maybe_request_file_write_approval(
    *,
    ctx: ToolExecutionContext,
    tool_path: str,
    action: str,
) -> None:
    permission_state = ctx.deps.permission_state
    resolved_path = resolve_workspace_path(
        workspace_root=ctx.deps.workspace_root,
        tool_path=tool_path,
    )
    outside_workspace = not path_is_within_workspace(
        workspace_root=ctx.deps.workspace_root,
        resolved_path=resolved_path,
    )
    approval_required = permission_state.approval_policy.mode == "always" or (
        permission_state.approval_policy.mode == "on_escalation"
        and permission_state.sandbox_policy.mode == "workspace_write"
        and outside_workspace
    )
    if not approval_required:
        return
    if ctx.deps.approval_requester is None:
        raise RuntimeError(
            f"{action.capitalize()} requires approval, but no approval "
            "requester is configured"
        )

    reason_prefix = (
        f"allow {action} outside workspace"
        if outside_workspace
        else f"allow {action}"
    )
    requested_permissions = (
        AdditionalSandboxPermissions(
            extra_write_roots=(str(resolved_path),),
        )
        if outside_workspace
        else None
    )
    decision = await ctx.deps.approval_requester(
        ApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            reason=(
                f"{reason_prefix}: {truncate_activity_label(tool_path)}"
            ),
            requested_capabilities=derive_requested_capabilities(
                permission_state=permission_state,
                additional_permissions=requested_permissions,
            ),
            requested_permissions=requested_permissions,
        )
    )
    if decision.decision != "approved":
        raise RuntimeError(
            f"{action.capitalize()} approval did not return an approved decision"
        )


__all__ = ["maybe_request_file_write_approval"]
