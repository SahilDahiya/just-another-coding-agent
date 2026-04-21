from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from just_another_coding_agent.contracts.platform import ShellFamily

SandboxNetworkAccess = Literal["restricted", "enabled"]
FilesystemAccess = Literal["read_only", "workspace_write", "full_access"]
ExecutionIsolation = Literal["sandboxed", "unsandboxed"]
ApprovalMode = Literal["never", "on_escalation", "always"]
ApprovalDecisionValue = Literal["approved", "denied"]
AdditionalNetworkAccess = Literal["enabled"]
PermissionGrantScope = Literal["once", "session"]
ApprovalRequestKind = Literal[
    "command_execution",
    "file_change",
    "permission_grant",
]
FileChangeKind = Literal["write", "edit"]
PermissionGrantKind = Literal["filesystem_read", "filesystem_write", "network_access"]


class _SandboxContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReadOnlySandboxPolicy(_SandboxContractModel):
    mode: Literal["read_only"] = "read_only"
    network_access: SandboxNetworkAccess = "restricted"

    @model_validator(mode="after")
    def _validate_network_access(self) -> "ReadOnlySandboxPolicy":
        if self.network_access != "restricted":
            raise ValueError(
                "read_only sandbox policy requires restricted network access"
            )
        return self


class WorkspaceWriteSandboxPolicy(_SandboxContractModel):
    mode: Literal["workspace_write"] = "workspace_write"
    network_access: SandboxNetworkAccess = "restricted"


class DangerFullAccessSandboxPolicy(_SandboxContractModel):
    mode: Literal["danger_full_access"] = "danger_full_access"
    network_access: SandboxNetworkAccess = "enabled"

    @model_validator(mode="after")
    def _validate_network_access(self) -> "DangerFullAccessSandboxPolicy":
        if self.network_access != "enabled":
            raise ValueError(
                "danger_full_access sandbox policy requires enabled network access"
            )
        return self


class ExternalSandboxPolicy(_SandboxContractModel):
    mode: Literal["external"] = "external"
    network_access: SandboxNetworkAccess = "restricted"


SandboxPolicy = Annotated[
    ReadOnlySandboxPolicy
    | WorkspaceWriteSandboxPolicy
    | DangerFullAccessSandboxPolicy
    | ExternalSandboxPolicy,
    Field(discriminator="mode"),
]


class ApprovalPolicy(_SandboxContractModel):
    mode: ApprovalMode


class EffectiveCapabilities(_SandboxContractModel):
    filesystem_access: FilesystemAccess
    network_access: SandboxNetworkAccess
    execution_isolation: ExecutionIsolation
    approval_mode: ApprovalMode


class AdditionalSandboxPermissions(_SandboxContractModel):
    network_access: AdditionalNetworkAccess | None = None
    extra_read_roots: tuple[str, ...] = ()
    extra_write_roots: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_non_empty_delta(self) -> "AdditionalSandboxPermissions":
        if (
            self.network_access is None
            and not self.extra_read_roots
            and not self.extra_write_roots
        ):
            raise ValueError(
                "additional sandbox permissions must request at least one "
                "permission delta"
            )
        return self


class SandboxPermissionGrant(_SandboxContractModel):
    permissions: AdditionalSandboxPermissions
    scope: PermissionGrantScope
    command_prefix: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_command_prefix(self) -> "SandboxPermissionGrant":
        if not self.command_prefix:
            return self
        if self.permissions.network_access != "enabled":
            raise ValueError(
                "command_prefix grants require enabled network access permissions"
            )
        return self


class FileSystemSandboxPolicy(_SandboxContractModel):
    access: FilesystemAccess
    extra_read_roots: tuple[str, ...] = ()
    extra_write_roots: tuple[str, ...] = ()


class NetworkSandboxPolicy(_SandboxContractModel):
    access: SandboxNetworkAccess


class NormalizedSandboxPolicy(_SandboxContractModel):
    filesystem: FileSystemSandboxPolicy
    network: NetworkSandboxPolicy
    execution_isolation: ExecutionIsolation


class PermissionState(_SandboxContractModel):
    sandbox_policy: SandboxPolicy
    approval_policy: ApprovalPolicy
    effective_capabilities: EffectiveCapabilities


class _ApprovalRequestBase(_SandboxContractModel):
    request_id: str
    request_kind: ApprovalRequestKind
    reason: str
    requested_capabilities: EffectiveCapabilities
    requested_permissions: AdditionalSandboxPermissions | None = None
    requested_grants: tuple[SandboxPermissionGrant, ...] = ()
    display_subject: str | None = None
    options: tuple["ApprovalOption", ...] = ()

    @model_validator(mode="after")
    def _validate_requested_grants(self) -> "_ApprovalRequestBase":
        if self.requested_permissions is None and not self.requested_grants:
            return self
        if self.requested_permissions is None:
            raise ValueError(
                "requested_grants require requested_permissions to be present"
            )
        if not self.requested_grants:
            raise ValueError(
                "requested_permissions require requested_grants to be present"
            )
        if (
            _flatten_permission_grants(self.requested_grants)
            != self.requested_permissions
        ):
            raise ValueError(
                "requested_permissions must match the flattened requested_grants"
            )
        if self.options:
            option_ids: set[str] = set()
            for option in self.options:
                if option.option_id in option_ids:
                    raise ValueError(
                        "approval request options must have unique option_id values"
                    )
                option_ids.add(option.option_id)
        return self


class ApprovalOption(_SandboxContractModel):
    option_id: str
    label: str
    decision: ApprovalDecisionValue
    granted_permissions: AdditionalSandboxPermissions | None = None
    granted_grants: tuple[SandboxPermissionGrant, ...] = ()

    @model_validator(mode="after")
    def _validate_granted_permissions(self) -> "ApprovalOption":
        if self.decision == "denied":
            if self.granted_permissions is not None or self.granted_grants:
                raise ValueError(
                    "Denied approval options cannot include granted permissions"
                )
            return self
        if self.granted_permissions is None and not self.granted_grants:
            return self
        if self.granted_permissions is None:
            raise ValueError(
                "granted_grants require granted_permissions to be present"
            )
        if not self.granted_grants:
            raise ValueError(
                "granted_permissions require granted_grants to be present"
            )
        if _flatten_permission_grants(self.granted_grants) != self.granted_permissions:
            raise ValueError(
                "granted_permissions must match the flattened granted_grants"
            )
        return self


class CommandExecutionApprovalRequest(_ApprovalRequestBase):
    request_kind: Literal["command_execution"] = "command_execution"
    command: str
    cwd: str
    shell_family: ShellFamily


class FileChangeApprovalRequest(_ApprovalRequestBase):
    request_kind: Literal["file_change"] = "file_change"
    path: str
    change_kind: FileChangeKind


class PermissionGrantApprovalRequest(_ApprovalRequestBase):
    request_kind: Literal["permission_grant"] = "permission_grant"
    grant_kind: PermissionGrantKind
    target: str | None = None


ApprovalRequest = Annotated[
    CommandExecutionApprovalRequest
    | FileChangeApprovalRequest
    | PermissionGrantApprovalRequest,
    Field(discriminator="request_kind"),
]


class ApprovalDecision(_SandboxContractModel):
    request_id: str
    decision: ApprovalDecisionValue
    option_id: str | None = None
    granted_permissions: AdditionalSandboxPermissions | None = None
    granted_grants: tuple[SandboxPermissionGrant, ...] = ()

    @model_validator(mode="after")
    def _validate_granted_permissions(self) -> "ApprovalDecision":
        if self.decision == "denied":
            if self.granted_permissions is not None or self.granted_grants:
                raise ValueError(
                    "Denied approval decisions cannot include granted permissions"
                )
            return self
        if self.granted_permissions is None and not self.granted_grants:
            return self
        if self.granted_permissions is None:
            raise ValueError(
                "granted_grants require granted_permissions to be present"
            )
        if not self.granted_grants:
            raise ValueError(
                "granted_permissions require granted_grants to be present"
            )
        if _flatten_permission_grants(self.granted_grants) != self.granted_permissions:
            raise ValueError(
                "granted_permissions must match the flattened granted_grants"
            )
        return self


def _flatten_permission_grants(
    grants: tuple[SandboxPermissionGrant, ...],
) -> AdditionalSandboxPermissions | None:
    if not grants:
        return None
    network_access: AdditionalNetworkAccess | None = None
    extra_read_roots: list[str] = []
    extra_write_roots: list[str] = []
    for grant in grants:
        if grant.permissions.network_access is not None:
            network_access = grant.permissions.network_access
        extra_read_roots.extend(grant.permissions.extra_read_roots)
        extra_write_roots.extend(grant.permissions.extra_write_roots)
    deduped_read_roots = tuple(dict.fromkeys(extra_read_roots))
    deduped_write_roots = tuple(dict.fromkeys(extra_write_roots))
    return AdditionalSandboxPermissions(
        network_access=network_access,
        extra_read_roots=deduped_read_roots,
        extra_write_roots=deduped_write_roots,
    )


def normalize_approval_decision(
    *,
    request: ApprovalRequest,
    decision: ApprovalDecision,
) -> ApprovalDecision:
    if decision.request_id != request.request_id:
        raise ValueError(
            "Approval decision request_id must match the approval request"
        )
    selected_option = _resolve_selected_approval_option(
        request=request,
        decision=decision,
    )
    if selected_option is not None:
        if selected_option.decision == "denied":
            return ApprovalDecision(
                request_id=decision.request_id,
                decision="denied",
                option_id=selected_option.option_id,
            )
        return ApprovalDecision(
            request_id=decision.request_id,
            decision="approved",
            option_id=selected_option.option_id,
            granted_permissions=selected_option.granted_permissions,
            granted_grants=selected_option.granted_grants,
        )
    if decision.decision == "denied":
        return decision
    if request.requested_permissions is None:
        if decision.granted_permissions is not None or decision.granted_grants:
            raise ValueError(
                "Approved decision cannot include granted permissions when "
                "the request did not ask for permission deltas"
            )
        return decision
    if not request.requested_grants:
        raise ValueError(
            "Approval requests with requested_permissions must declare "
            "requested_grants"
        )
    if decision.granted_permissions is None and not decision.granted_grants:
        return ApprovalDecision(
            request_id=decision.request_id,
            decision=decision.decision,
            granted_permissions=request.requested_permissions,
            granted_grants=request.requested_grants,
        )
    if (
        decision.granted_permissions != request.requested_permissions
        or decision.granted_grants != request.requested_grants
    ):
        raise ValueError(
            "Approved decisions with explicit grants must match requested_grants"
        )
    return decision


def _resolve_selected_approval_option(
    *,
    request: ApprovalRequest,
    decision: ApprovalDecision,
) -> ApprovalOption | None:
    if not request.options:
        return None
    if decision.option_id is not None:
        for option in request.options:
            if option.option_id != decision.option_id:
                continue
            if option.decision != decision.decision:
                raise ValueError(
                    "Approval decision option_id must match the decision value"
                )
            return option
        raise ValueError(
            "Approval decision option_id must reference a request approval option"
        )
    if decision.decision == "denied":
        denied_options = tuple(
            option for option in request.options if option.decision == "denied"
        )
        if len(denied_options) == 1:
            return denied_options[0]
        return None
    if decision.granted_permissions is not None or decision.granted_grants:
        for option in request.options:
            if option.decision != "approved":
                continue
            if (
                option.granted_permissions == decision.granted_permissions
                and option.granted_grants == decision.granted_grants
            ):
                return option
        raise ValueError(
            "Approved decisions with explicit grants must match an approval option"
        )
    return None


def derive_effective_capabilities(
    *,
    sandbox_policy: SandboxPolicy,
    approval_policy: ApprovalPolicy,
) -> EffectiveCapabilities:
    if isinstance(sandbox_policy, ReadOnlySandboxPolicy):
        filesystem_access: FilesystemAccess = "read_only"
        execution_isolation: ExecutionIsolation = "sandboxed"
    elif isinstance(sandbox_policy, WorkspaceWriteSandboxPolicy):
        filesystem_access = "workspace_write"
        execution_isolation = "sandboxed"
    elif isinstance(sandbox_policy, DangerFullAccessSandboxPolicy):
        filesystem_access = "full_access"
        execution_isolation = "unsandboxed"
    else:
        filesystem_access = "full_access"
        execution_isolation = "sandboxed"

    return EffectiveCapabilities(
        filesystem_access=filesystem_access,
        network_access=sandbox_policy.network_access,
        execution_isolation=execution_isolation,
        approval_mode=approval_policy.mode,
    )


def derive_normalized_sandbox_policy(
    *,
    permission_state: PermissionState,
    additional_permissions: AdditionalSandboxPermissions | None = None,
) -> NormalizedSandboxPolicy:
    base_capabilities = derive_effective_capabilities(
        sandbox_policy=permission_state.sandbox_policy,
        approval_policy=permission_state.approval_policy,
    )
    extra_read_roots: tuple[str, ...] = ()
    extra_write_roots: tuple[str, ...] = ()
    network_access = base_capabilities.network_access

    if additional_permissions is not None:
        extra_read_roots = additional_permissions.extra_read_roots
        extra_write_roots = additional_permissions.extra_write_roots
        if additional_permissions.network_access is not None:
            network_access = additional_permissions.network_access

    return NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(
            access=base_capabilities.filesystem_access,
            extra_read_roots=extra_read_roots,
            extra_write_roots=extra_write_roots,
        ),
        network=NetworkSandboxPolicy(access=network_access),
        execution_isolation=base_capabilities.execution_isolation,
    )


def derive_requested_capabilities(
    *,
    permission_state: PermissionState,
    additional_permissions: AdditionalSandboxPermissions | None = None,
) -> EffectiveCapabilities:
    normalized_policy = derive_normalized_sandbox_policy(
        permission_state=permission_state,
        additional_permissions=additional_permissions,
    )
    return EffectiveCapabilities(
        filesystem_access=normalized_policy.filesystem.access,
        network_access=normalized_policy.network.access,
        execution_isolation=normalized_policy.execution_isolation,
        approval_mode=permission_state.approval_policy.mode,
    )


def build_permission_state(
    *,
    sandbox_policy: SandboxPolicy,
    approval_policy: ApprovalPolicy,
    effective_capabilities: EffectiveCapabilities | None = None,
) -> PermissionState:
    return PermissionState(
        sandbox_policy=sandbox_policy,
        approval_policy=approval_policy,
        effective_capabilities=(
            effective_capabilities
            if effective_capabilities is not None
            else derive_effective_capabilities(
                sandbox_policy=sandbox_policy,
                approval_policy=approval_policy,
            )
        ),
    )


def build_default_permission_state() -> PermissionState:
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
        effective_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="restricted",
            execution_isolation="unsandboxed",
            approval_mode="on_escalation",
        ),
    )


__all__ = [
    "ApprovalDecision",
    "ApprovalDecisionValue",
    "ApprovalMode",
    "ApprovalOption",
    "ApprovalPolicy",
    "ApprovalRequest",
    "ApprovalRequestKind",
    "AdditionalNetworkAccess",
    "AdditionalSandboxPermissions",
    "CommandExecutionApprovalRequest",
    "DangerFullAccessSandboxPolicy",
    "EffectiveCapabilities",
    "ExecutionIsolation",
    "ExternalSandboxPolicy",
    "FileChangeApprovalRequest",
    "FileChangeKind",
    "FileSystemSandboxPolicy",
    "FilesystemAccess",
    "NetworkSandboxPolicy",
    "NormalizedSandboxPolicy",
    "PermissionState",
    "PermissionGrantApprovalRequest",
    "PermissionGrantKind",
    "PermissionGrantScope",
    "ReadOnlySandboxPolicy",
    "SandboxPermissionGrant",
    "SandboxNetworkAccess",
    "SandboxPolicy",
    "WorkspaceWriteSandboxPolicy",
    "build_default_permission_state",
    "build_permission_state",
    "derive_effective_capabilities",
    "derive_normalized_sandbox_policy",
    "derive_requested_capabilities",
    "normalize_approval_decision",
]
