from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    EffectiveCapabilities,
    NormalizedSandboxPolicy,
)

ApprovalDisposition = Literal["allowed", "prompt", "denied_by_policy"]


@dataclass(frozen=True)
class SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None
    requested_capabilities: EffectiveCapabilities
    normalized_policy: NormalizedSandboxPolicy
    approval_disposition: ApprovalDisposition


__all__ = ["ApprovalDisposition", "SandboxExecutionPlan"]
