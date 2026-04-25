from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    AdditionalNetworkAccess,
    AdditionalSandboxPermissions,
    ApprovalOption,
    ApprovalRequestKind,
    FileChangeApprovalRequest,
    FileSystemSandboxPolicy,
    PermissionGrantApprovalRequest,
    PermissionGrantScope,
    PermissionState,
    SandboxPermissionGrant,
    approval_mode_for_request_kind,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
    describe_approval_policy_for_request_kind,
)
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan
from just_another_coding_agent.contracts.tool_runtime import (
    ExecApprovalRequirement,
    ForbiddenApproval,
    NeedsApproval,
    SkipApproval,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools._approval_flow import (
    fulfill_approval_requirement,
)
from just_another_coding_agent.tools._permission_actions import (
    approval_scope_root,
    filesystem_path_permission_action,
)
from just_another_coding_agent.tools._policy_engine import (
    PermissionAction,
    evaluate_permission_actions,
)
from just_another_coding_agent.tools._shell_permissions import (
    extract_shell_permission_actions,
)
from just_another_coding_agent.tools._shell_permissions import (
    shell_network_command_prefix as _shell_network_command_prefix,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps


class ToolExecutionContext(Protocol):
    deps: WorkspaceDeps


@dataclass(frozen=True)
class FileAccessPlan:
    sandbox_plan: SandboxExecutionPlan
    effective_permissions: AdditionalSandboxPermissions | None
    tool_path: str | None
    action: str
    access_kind: FileAccessKind
    request_kind: ApprovalRequestKind
    approval_scope_root: str | None
    approval_policy_label: str


@dataclass(frozen=True)
class FileAccessRuntime:
    file_access_plan: FileAccessPlan
    requirement: ExecApprovalRequirement

    @property
    def sandbox_plan(self) -> SandboxExecutionPlan:
        return self.file_access_plan.sandbox_plan

    def approval_requirement(self) -> ExecApprovalRequirement:
        return self.requirement

    async def run(
        self,
        ctx: ToolExecutionContext | None,
    ) -> FileAccessPlan:
        del ctx
        return self.file_access_plan


FileAccessKind = Literal["read", "write"]
FileToolActionSource = Literal["read_tool", "write_tool", "edit_tool"]


def _approval_scope_root(resolved_path: Path) -> str:
    return approval_scope_root(resolved_path)


def describe_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
    *,
    write_label: str = "writable roots",
) -> str:
    if permissions is None:
        return ""
    segments: list[str] = []
    if permissions.network_access == "enabled":
        segments.append("network enabled")
    if permissions.extra_read_roots:
        joined = ", ".join(permissions.extra_read_roots)
        segments.append(f"read-only roots: {joined}")
    if permissions.extra_write_roots:
        joined = ", ".join(permissions.extra_write_roots)
        segments.append(f"{write_label}: {joined}")
    return "; ".join(segments)


def describe_shell_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
) -> str:
    return describe_permission_delta(
        permissions, write_label="outside-workspace writes"
    )


def _approval_denied_message(
    *,
    request: FileChangeApprovalRequest | PermissionGrantApprovalRequest,
) -> str:
    if request.request_kind == "file_change":
        return (
            f"Approval denied: {request.reason}. "
            "The file was not modified. Choose another approach or stop."
        )
    return (
        f"Approval denied: {request.reason}. "
        "The file was not read. Choose another approach or stop."
    )


def _policy_denied_message(
    *,
    request: FileChangeApprovalRequest | PermissionGrantApprovalRequest,
) -> str:
    if request.request_kind == "file_change":
        return (
            f"Approval blocked by current policy: {request.reason}. "
            "The file was not modified. Choose another approach or stop."
        )
    return (
        f"Approval blocked by current policy: {request.reason}. "
        "The file was not read. Choose another approach or stop."
    )


def _file_action_label(*, action: str, tool_path: str | None) -> str:
    if tool_path is None:
        return action
    return truncate_activity_label(tool_path)


def _file_action_subject(*, action: str, tool_path: str | None) -> str:
    if tool_path is None:
        return action
    return f"{action} {tool_path}"


def _file_access_approval_reason(file_access_plan: FileAccessPlan) -> str:
    target_label = _file_action_label(
        action=file_access_plan.action,
        tool_path=file_access_plan.tool_path,
    )
    if file_access_plan.sandbox_plan.requested_permissions is None:
        return (
            f"allow {file_access_plan.action}: {target_label} "
            f"(approval policy: {file_access_plan.approval_policy_label})"
        )

    reason = f"allow {file_access_plan.action} outside workspace: {target_label}"
    permission_detail = describe_permission_delta(
        file_access_plan.sandbox_plan.requested_permissions
    )
    if permission_detail:
        reason = f"{reason} ({permission_detail})"
    return reason


def extract_file_permission_actions(
    *,
    permission_state: PermissionState,
    tool_path: str,
    action_source: FileToolActionSource,
    access_kind: FileAccessKind,
    workspace_root: Path,
    permission_memory,
) -> tuple[PermissionAction, ...]:
    return (
        filesystem_path_permission_action(
            action_kind=(
                "filesystem_read" if access_kind == "read" else "filesystem_write"
            ),
            source=action_source,
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            tool_path=tool_path,
            extracted_by="tool_path_resolution",
        ),
    )


def derive_sandbox_execution_plan(
    *,
    permission_state: PermissionState,
    request_kind: ApprovalRequestKind,
    effective_permissions: AdditionalSandboxPermissions | None = None,
    approval_permissions: AdditionalSandboxPermissions | None = None,
) -> SandboxExecutionPlan:
    approval_mode = approval_mode_for_request_kind(
        approval_policy=permission_state.approval_policy,
        request_kind=request_kind,
    )
    if approval_mode == "always":
        approval_disposition = "prompt"
    elif approval_permissions is None:
        approval_disposition = "allowed"
    elif approval_mode == "on_escalation":
        approval_disposition = "prompt"
    else:
        approval_disposition = "denied_by_policy"
    normalized_permissions = (
        effective_permissions
        if approval_disposition == "allowed" and effective_permissions is not None
        else None
    )
    return SandboxExecutionPlan(
        requested_permissions=approval_permissions,
        requested_capabilities=derive_requested_capabilities(
            permission_state=permission_state,
            additional_permissions=(
                effective_permissions
                if approval_disposition == "allowed"
                and effective_permissions is not None
                else approval_permissions
            ),
        ),
        normalized_policy=derive_normalized_sandbox_policy(
            permission_state=permission_state,
            additional_permissions=normalized_permissions,
        ),
        approval_disposition=approval_disposition,
    )


def _approval_option(
    *,
    option_id: str,
    label: str,
    decision: Literal["approved", "denied"],
    granted_permissions: AdditionalSandboxPermissions | None = None,
    granted_grants: tuple[SandboxPermissionGrant, ...] = (),
) -> ApprovalOption:
    return ApprovalOption(
        option_id=option_id,
        label=label,
        decision=decision,
        granted_permissions=granted_permissions,
        granted_grants=granted_grants,
    )


def _filesystem_session_option_label(
    *,
    access_kind: Literal["read", "write"],
    root: str,
) -> str:
    verb = "reads" if access_kind == "read" else "writes"
    return f"Allow {verb} under {root} for this session"


def _shell_network_session_option_label(command_prefix: tuple[str, ...]) -> str:
    return f"Allow {' '.join(command_prefix)} for this session"


def build_permission_grants(
    *,
    permissions: AdditionalSandboxPermissions | None,
    network_scope: PermissionGrantScope = "once",
    filesystem_scope: PermissionGrantScope = "session",
    network_command_prefix: tuple[str, ...] = (),
) -> tuple[SandboxPermissionGrant, ...]:
    if permissions is None:
        return ()
    grants: list[SandboxPermissionGrant] = []
    if permissions.network_access is not None:
        grants.append(
            SandboxPermissionGrant(
                permissions=AdditionalSandboxPermissions(
                    network_access=permissions.network_access,
                ),
                scope=network_scope,
                command_prefix=(
                    network_command_prefix if network_scope == "session" else ()
                ),
            )
        )
    if permissions.extra_read_roots or permissions.extra_write_roots:
        grants.append(
            SandboxPermissionGrant(
                permissions=AdditionalSandboxPermissions(
                    extra_read_roots=permissions.extra_read_roots,
                    extra_write_roots=permissions.extra_write_roots,
                ),
                scope=filesystem_scope,
            )
        )
    return tuple(grants)


def build_permission_approval_options(
    *,
    permissions: AdditionalSandboxPermissions,
    once_label: str,
    session_label: str | None = None,
    network_command_prefix: tuple[str, ...] = (),
) -> tuple[ApprovalOption, ...]:
    options = [
        _approval_option(
            option_id="allow-once",
            label=once_label,
            decision="approved",
            granted_permissions=permissions,
            granted_grants=build_permission_grants(
                permissions=permissions,
                network_scope="once",
                filesystem_scope="once",
            ),
        )
    ]
    if session_label is not None:
        options.append(
            _approval_option(
                option_id="allow-session",
                label=session_label,
                decision="approved",
                granted_permissions=permissions,
                granted_grants=build_permission_grants(
                    permissions=permissions,
                    network_scope="session",
                    filesystem_scope="session",
                    network_command_prefix=network_command_prefix,
                ),
            )
        )
    options.append(
        _approval_option(
            option_id="deny",
            label="Deny",
            decision="denied",
        )
    )
    return tuple(options)


def build_shell_approval_options(
    *,
    command: str,
    shell_family: ShellFamily,
    permissions: AdditionalSandboxPermissions,
) -> tuple[ApprovalOption, ...]:
    session_label: str | None = None
    network_prefix = _shell_network_command_prefix(
        command,
        shell_family=shell_family,
    )
    if (
        permissions.network_access is not None
        and not permissions.extra_read_roots
        and not permissions.extra_write_roots
        and network_prefix
    ):
        session_label = _shell_network_session_option_label(network_prefix)
    elif (
        len(permissions.extra_read_roots) == 1
        and permissions.network_access is None
        and not permissions.extra_write_roots
    ):
        session_label = _filesystem_session_option_label(
            access_kind="read",
            root=permissions.extra_read_roots[0],
        )
    elif (
        len(permissions.extra_write_roots) == 1
        and permissions.network_access is None
        and not permissions.extra_read_roots
    ):
        session_label = _filesystem_session_option_label(
            access_kind="write",
            root=permissions.extra_write_roots[0],
        )

    return build_permission_approval_options(
        permissions=permissions,
        once_label="Allow once",
        session_label=session_label,
        network_command_prefix=network_prefix,
    )


def plan_shell_execution(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path,
    permission_memory,
) -> SandboxExecutionPlan:
    actions = extract_shell_permission_actions(
        permission_state=permission_state,
        command=command,
        shell_family=shell_family,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )
    evaluations = evaluate_permission_actions(actions=actions)
    approval_network_access: AdditionalNetworkAccess | None = None
    if any(
        evaluation.action.action_kind == "network_access"
        and evaluation.match.decision == "prompt"
        for evaluation in evaluations
    ):
        approval_network_access = "enabled"
    approval_read_roots = tuple(
        evaluation.action.root
        for evaluation in evaluations
        if evaluation.action.action_kind == "filesystem_read"
        and evaluation.action.path_scope == "non_workspace"
        and evaluation.match.decision == "prompt"
        and evaluation.action.root is not None
    )
    approval_write_roots = tuple(
        evaluation.action.root
        for evaluation in evaluations
        if evaluation.action.action_kind == "filesystem_write"
        and evaluation.action.path_scope == "non_workspace"
        and evaluation.match.decision == "prompt"
        and evaluation.action.root is not None
    )

    approval_permissions: AdditionalSandboxPermissions | None = None
    if (
        approval_network_access is not None
        or approval_read_roots
        or approval_write_roots
    ):
        approval_permissions = AdditionalSandboxPermissions(
            network_access=approval_network_access,
            extra_read_roots=approval_read_roots,
            extra_write_roots=approval_write_roots,
        )

    return derive_sandbox_execution_plan(
        permission_state=permission_state,
        request_kind="command_execution",
        effective_permissions=None,
        approval_permissions=approval_permissions,
    )


def plan_file_access(
    *,
    permission_state: PermissionState,
    tool_path: str | None,
    action: str,
    access_kind: FileAccessKind,
    workspace_root: Path,
    permission_memory,
) -> FileAccessPlan:
    request_kind: ApprovalRequestKind = (
        "permission_grant" if access_kind == "read" else "file_change"
    )
    outside_workspace = False
    approval_scope_root: str | None = None
    actions: tuple[PermissionAction, ...] = ()
    if tool_path is not None:
        action_source: FileToolActionSource = (
            "read_tool" if access_kind == "read" else f"{action}_tool"
        )
        actions = extract_file_permission_actions(
            permission_state=permission_state,
            tool_path=tool_path,
            action_source=action_source,
            access_kind=access_kind,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
        )
        if actions:
            outside_workspace = actions[0].path_scope == "non_workspace"
            approval_scope_root = actions[0].root

    evaluations = evaluate_permission_actions(actions=actions)

    effective_permissions: AdditionalSandboxPermissions | None = None
    if (
        permission_state.effective_capabilities.filesystem_access != "full_access"
        and approval_scope_root is not None
        and outside_workspace
    ):
        if access_kind == "read":
            effective_permissions = AdditionalSandboxPermissions(
                extra_read_roots=(approval_scope_root,),
            )
        else:
            effective_permissions = AdditionalSandboxPermissions(
                extra_write_roots=(approval_scope_root,),
            )

    prompted_roots = tuple(
        evaluation.action.root
        for evaluation in evaluations
        if evaluation.match.decision == "prompt" and evaluation.action.root is not None
    )
    approval_permissions: AdditionalSandboxPermissions | None = None
    if prompted_roots:
        if access_kind == "read":
            approval_permissions = AdditionalSandboxPermissions(
                extra_read_roots=prompted_roots,
            )
        else:
            approval_permissions = AdditionalSandboxPermissions(
                extra_write_roots=prompted_roots,
            )

    return FileAccessPlan(
        sandbox_plan=derive_sandbox_execution_plan(
            permission_state=permission_state,
            request_kind=request_kind,
            effective_permissions=effective_permissions,
            approval_permissions=approval_permissions,
        ),
        effective_permissions=effective_permissions,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        request_kind=request_kind,
        approval_scope_root=approval_scope_root,
        approval_policy_label=describe_approval_policy_for_request_kind(
            approval_policy=permission_state.approval_policy,
            request_kind=request_kind,
        ),
    )


def _build_file_access_approval_request(
    file_access_plan: FileAccessPlan,
) -> FileChangeApprovalRequest | PermissionGrantApprovalRequest:
    sandbox_plan = file_access_plan.sandbox_plan
    options: tuple[ApprovalOption, ...] = ()
    if (
        sandbox_plan.requested_permissions is not None
        and file_access_plan.approval_scope_root is not None
    ):
        options = build_permission_approval_options(
            permissions=sandbox_plan.requested_permissions,
            once_label="Allow once",
            session_label=_filesystem_session_option_label(
                access_kind=file_access_plan.access_kind,
                root=file_access_plan.approval_scope_root,
            ),
        )

    reason = _file_access_approval_reason(file_access_plan)
    display_subject = _file_action_subject(
        action=file_access_plan.action,
        tool_path=file_access_plan.tool_path,
    )
    if file_access_plan.access_kind == "read":
        return PermissionGrantApprovalRequest(
            request_id=f"{file_access_plan.action}-{uuid4().hex}",
            request_kind="permission_grant",
            reason=reason,
            grant_kind="filesystem_read",
            target=file_access_plan.approval_scope_root,
            requested_capabilities=sandbox_plan.requested_capabilities,
            requested_permissions=sandbox_plan.requested_permissions,
            display_subject=display_subject,
            requested_grants=build_permission_grants(
                permissions=sandbox_plan.requested_permissions,
                filesystem_scope="session",
            ),
            options=options,
        )

    return FileChangeApprovalRequest(
        request_id=f"{file_access_plan.action}-{uuid4().hex}",
        request_kind="file_change",
        reason=reason,
        path=file_access_plan.tool_path or "",
        change_kind=(
            file_access_plan.action
            if file_access_plan.action in {"write", "edit"}
            else "write"
        ),
        requested_capabilities=sandbox_plan.requested_capabilities,
        requested_permissions=sandbox_plan.requested_permissions,
        display_subject=display_subject,
        requested_grants=build_permission_grants(
            permissions=sandbox_plan.requested_permissions,
            filesystem_scope="session",
        ),
        options=options,
    )


def _build_file_access_approval_requirement(
    file_access_plan: FileAccessPlan,
) -> ExecApprovalRequirement:
    if file_access_plan.sandbox_plan.approval_disposition == "allowed":
        return SkipApproval()

    request = _build_file_access_approval_request(file_access_plan)
    if file_access_plan.sandbox_plan.approval_disposition == "denied_by_policy":
        return ForbiddenApproval(
            request=request,
            denied_message=_policy_denied_message(request=request),
        )

    return NeedsApproval(
        request=request,
        denied_message=_approval_denied_message(request=request),
        missing_requester_message=(
            f"{file_access_plan.action.capitalize()} requires approval, "
            "but no approval requester is configured"
        ),
    )


def build_file_access_runtime(
    *,
    permission_state: PermissionState,
    tool_path: str | None,
    action: str,
    access_kind: FileAccessKind,
    workspace_root: Path,
    permission_memory,
) -> FileAccessRuntime:
    file_access_plan = plan_file_access(
        permission_state=permission_state,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )
    return FileAccessRuntime(
        file_access_plan=file_access_plan,
        requirement=_build_file_access_approval_requirement(file_access_plan),
    )


async def approved_read_only_filesystem_policy(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
) -> FileSystemSandboxPolicy:
    file_access_plan = await _approved_file_access_plan(
        ctx=ctx,
        tool_path=tool_path,
        action=action,
        access_kind="read",
    )
    return derive_normalized_sandbox_policy(
        permission_state=ctx.deps.permission_state,
        additional_permissions=file_access_plan.effective_permissions,
    ).filesystem


async def maybe_request_file_write_approval(
    *,
    ctx: ToolExecutionContext,
    tool_path: str,
    action: str,
) -> None:
    await _approved_file_access_plan(
        ctx=ctx,
        tool_path=tool_path,
        action=action,
        access_kind="write",
    )


async def _approved_file_access_plan(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
    access_kind: FileAccessKind,
) -> FileAccessPlan:
    runtime = build_file_access_runtime(
        permission_state=ctx.deps.permission_state,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        workspace_root=ctx.deps.workspace_root,
        permission_memory=ctx.deps.permission_memory,
    )
    await fulfill_approval_requirement(
        ctx=ctx,
        requirement=runtime.approval_requirement(),
    )
    return await runtime.run(ctx)


__all__ = [
    "approved_read_only_filesystem_policy",
    "build_shell_approval_options",
    "build_file_access_runtime",
    "FileAccessPlan",
    "FileAccessRuntime",
    "SandboxExecutionPlan",
    "describe_permission_delta",
    "describe_shell_permission_delta",
    "derive_sandbox_execution_plan",
    "extract_file_permission_actions",
    "extract_shell_permission_actions",
    "maybe_request_file_write_approval",
    "plan_file_access",
    "plan_shell_execution",
    "build_permission_grants",
]
