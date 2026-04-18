from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalRequest,
    EffectiveCapabilities,
    FileSystemSandboxPolicy,
    NormalizedSandboxPolicy,
    PermissionState,
    derive_normalized_sandbox_policy,
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


_NETWORK_COMMANDS = frozenset(
    {
        "curl",
        "dig",
        "gh",
        "host",
        "nc",
        "nslookup",
        "ping",
        "scp",
        "sftp",
        "ssh",
        "telnet",
        "wget",
    }
)
_GIT_NETWORK_SUBCOMMANDS = frozenset(
    {
        "clone",
        "fetch",
        "ls-remote",
        "pull",
        "push",
    }
)


@dataclass(frozen=True)
class SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None
    requested_capabilities: EffectiveCapabilities
    normalized_policy: NormalizedSandboxPolicy
    approval_required: bool


def _shell_command_requests_network_access(
    *,
    command: str,
    shell_family: ShellFamily,
) -> bool:
    if shell_family != "posix":
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = tokens[0]
    if executable in _NETWORK_COMMANDS:
        return True
    if executable == "git" and len(tokens) > 1:
        return tokens[1] in _GIT_NETWORK_SUBCOMMANDS
    return False


def derive_sandbox_execution_plan(
    *,
    permission_state: PermissionState,
    requested_permissions: AdditionalSandboxPermissions | None = None,
) -> SandboxExecutionPlan:
    approval_required = permission_state.approval_policy.mode == "always" or (
        permission_state.approval_policy.mode == "on_escalation"
        and requested_permissions is not None
    )
    return SandboxExecutionPlan(
        requested_permissions=requested_permissions,
        requested_capabilities=derive_requested_capabilities(
            permission_state=permission_state,
            additional_permissions=requested_permissions,
        ),
        normalized_policy=derive_normalized_sandbox_policy(
            permission_state=permission_state,
            additional_permissions=requested_permissions,
        ),
        approval_required=approval_required,
    )


def plan_shell_execution(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
) -> SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None = None
    if (
        permission_state.approval_policy.mode == "on_escalation"
        and permission_state.sandbox_policy.mode == "workspace_write"
        and _shell_command_requests_network_access(
            command=command,
            shell_family=shell_family,
        )
    ):
        requested_permissions = AdditionalSandboxPermissions(
            network_access="enabled",
        )
    return derive_sandbox_execution_plan(
        permission_state=permission_state,
        requested_permissions=requested_permissions,
    )


async def approved_read_only_filesystem_policy(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
) -> FileSystemSandboxPolicy:
    permission_state = ctx.deps.permission_state
    requested_permissions: AdditionalSandboxPermissions | None = None
    outside_workspace = False
    resolved_path: str | None = None
    if tool_path is not None:
        resolved = resolve_workspace_path(
            workspace_root=ctx.deps.workspace_root,
            tool_path=tool_path,
        )
        resolved_path = str(resolved)
        outside_workspace = not path_is_within_workspace(
            workspace_root=ctx.deps.workspace_root,
            resolved_path=resolved,
        )
        if outside_workspace:
            requested_permissions = AdditionalSandboxPermissions(
                extra_read_roots=(resolved_path,),
            )
    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        requested_permissions=requested_permissions,
    )
    if not plan.approval_required:
        return plan.normalized_policy.filesystem
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
    decision = await ctx.deps.approval_requester(
        ApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            reason=(
                f"{reason_prefix}: "
                f"{truncate_activity_label(tool_path or resolved_path or action)}"
            ),
            requested_capabilities=plan.requested_capabilities,
            requested_permissions=plan.requested_permissions,
        )
    )
    if decision.decision != "approved":
        raise RuntimeError(
            f"{action.capitalize()} approval did not return an approved decision"
        )
    return plan.normalized_policy.filesystem


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
    requested_permissions = (
        AdditionalSandboxPermissions(
            extra_write_roots=(str(resolved_path),),
        )
        if outside_workspace
        else None
    )
    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        requested_permissions=requested_permissions,
    )
    if not plan.approval_required:
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
    decision = await ctx.deps.approval_requester(
        ApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            reason=(
                f"{reason_prefix}: {truncate_activity_label(tool_path)}"
            ),
            requested_capabilities=plan.requested_capabilities,
            requested_permissions=plan.requested_permissions,
        )
    )
    if decision.decision != "approved":
        raise RuntimeError(
            f"{action.capitalize()} approval did not return an approved decision"
        )


__all__ = [
    "approved_read_only_filesystem_policy",
    "SandboxExecutionPlan",
    "derive_sandbox_execution_plan",
    "maybe_request_file_write_approval",
    "plan_shell_execution",
]
