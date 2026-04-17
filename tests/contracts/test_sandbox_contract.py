from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    DangerFullAccessSandboxPolicy,
    EffectiveCapabilities,
    ExternalSandboxPolicy,
    ReadOnlySandboxPolicy,
    SandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    derive_effective_capabilities,
)


def test_sandbox_policy_accepts_named_modes_with_explicit_defaults() -> None:
    adapter = TypeAdapter(SandboxPolicy)

    read_only = adapter.validate_python({"mode": "read_only"})
    assert read_only == ReadOnlySandboxPolicy()

    workspace_write = adapter.validate_python({"mode": "workspace_write"})
    assert workspace_write == WorkspaceWriteSandboxPolicy()

    external = adapter.validate_python({"mode": "external"})
    assert external == ExternalSandboxPolicy()

    danger_full_access = adapter.validate_python({"mode": "danger_full_access"})
    assert danger_full_access == DangerFullAccessSandboxPolicy()


def test_sandbox_policy_rejects_invalid_network_combinations() -> None:
    adapter = TypeAdapter(SandboxPolicy)

    with pytest.raises(ValidationError, match="read_only"):
        adapter.validate_python(
            {"mode": "read_only", "network_access": "enabled"}
        )

    with pytest.raises(ValidationError, match="danger_full_access"):
        adapter.validate_python(
            {
                "mode": "danger_full_access",
                "network_access": "restricted",
            }
        )


def test_approval_policy_accepts_only_named_modes() -> None:
    policy = ApprovalPolicy(mode="on_escalation")
    assert policy.mode == "on_escalation"

    with pytest.raises(ValidationError, match="Input should be"):
        ApprovalPolicy(mode="sometimes")  # type: ignore[arg-type]


def test_derive_effective_capabilities_normalizes_policy_shapes() -> None:
    sandbox = WorkspaceWriteSandboxPolicy(network_access="enabled")
    approval = ApprovalPolicy(mode="always")

    assert derive_effective_capabilities(
        sandbox_policy=sandbox,
        approval_policy=approval,
    ) == EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="enabled",
        execution_isolation="sandboxed",
        approval_mode="always",
    )

    assert derive_effective_capabilities(
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    ) == EffectiveCapabilities(
        filesystem_access="full_access",
        network_access="enabled",
        execution_isolation="unsandboxed",
        approval_mode="never",
    )


def test_approval_request_and_decision_are_strict_contract_models() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="full_access",
        network_access="enabled",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
    )

    request = ApprovalRequest(
        request_id="approval-1",
        reason="Enable network for a package install.",
        requested_capabilities=capabilities,
    )
    assert request.requested_capabilities == capabilities

    decision = ApprovalDecision(
        request_id="approval-1",
        decision="approved",
    )
    assert decision.decision == "approved"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ApprovalRequest(
            request_id="approval-2",
            reason="bad",
            requested_capabilities=capabilities,
            extra_field=True,
        )
