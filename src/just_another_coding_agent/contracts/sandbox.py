from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SandboxNetworkAccess = Literal["restricted", "enabled"]
FilesystemAccess = Literal["read_only", "workspace_write", "full_access"]
ExecutionIsolation = Literal["sandboxed", "unsandboxed"]
ApprovalMode = Literal["never", "on_escalation", "always"]
ApprovalDecisionValue = Literal["approved", "denied"]


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


class ApprovalRequest(_SandboxContractModel):
    request_id: str
    reason: str
    requested_capabilities: EffectiveCapabilities


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


__all__ = [
    "ApprovalDecision",
    "ApprovalDecisionValue",
    "ApprovalMode",
    "ApprovalPolicy",
    "ApprovalRequest",
    "DangerFullAccessSandboxPolicy",
    "EffectiveCapabilities",
    "ExecutionIsolation",
    "ExternalSandboxPolicy",
    "FilesystemAccess",
    "ReadOnlySandboxPolicy",
    "SandboxNetworkAccess",
    "SandboxPolicy",
    "WorkspaceWriteSandboxPolicy",
    "derive_effective_capabilities",
]
