from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SandboxNetworkAccess = Literal["restricted", "enabled"]
FilesystemAccess = Literal["read_only", "workspace_write", "full_access"]
ExecutionIsolation = Literal["sandboxed", "unsandboxed"]
ApprovalMode = Literal["never", "on_escalation", "always"]
ApprovalDecisionValue = Literal["approved", "denied"]
AdditionalNetworkAccess = Literal["enabled"]


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


class ApprovalRequest(_SandboxContractModel):
    request_id: str
    reason: str
    requested_capabilities: EffectiveCapabilities
    requested_permissions: AdditionalSandboxPermissions | None = None


class ApprovalDecision(_SandboxContractModel):
    request_id: str
    decision: ApprovalDecisionValue


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

    filesystem_access = base_capabilities.filesystem_access

    return NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(
            access=filesystem_access,
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
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    )


__all__ = [
    "ApprovalDecision",
    "ApprovalDecisionValue",
    "ApprovalMode",
    "ApprovalPolicy",
    "ApprovalRequest",
    "AdditionalNetworkAccess",
    "AdditionalSandboxPermissions",
    "DangerFullAccessSandboxPolicy",
    "EffectiveCapabilities",
    "ExecutionIsolation",
    "ExternalSandboxPolicy",
    "FileSystemSandboxPolicy",
    "FilesystemAccess",
    "NetworkSandboxPolicy",
    "NormalizedSandboxPolicy",
    "PermissionState",
    "ReadOnlySandboxPolicy",
    "SandboxNetworkAccess",
    "SandboxPolicy",
    "WorkspaceWriteSandboxPolicy",
    "build_default_permission_state",
    "build_permission_state",
    "derive_effective_capabilities",
    "derive_normalized_sandbox_policy",
    "derive_requested_capabilities",
]
