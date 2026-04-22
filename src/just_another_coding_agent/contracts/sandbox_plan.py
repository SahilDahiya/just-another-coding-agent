from __future__ import annotations

from dataclasses import dataclass

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    EffectiveCapabilities,
    NormalizedSandboxPolicy,
)


@dataclass(frozen=True)
class SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None
    requested_capabilities: EffectiveCapabilities
    normalized_policy: NormalizedSandboxPolicy
    approval_required: bool


__all__ = ["SandboxExecutionPlan"]
