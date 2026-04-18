from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    DangerFullAccessSandboxPolicy,
    EffectiveCapabilities,
    ExternalSandboxPolicy,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    NormalizedSandboxPolicy,
    ReadOnlySandboxPolicy,
    SandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
    derive_effective_capabilities,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
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
        requested_permissions=AdditionalSandboxPermissions(
            network_access="enabled"
        ),
    )
    assert request.requested_capabilities == capabilities
    assert request.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )

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


def test_additional_sandbox_permissions_require_a_non_empty_delta() -> None:
    with pytest.raises(ValidationError, match="must request at least one"):
        AdditionalSandboxPermissions()


def test_normalized_sandbox_policy_derives_network_and_filesystem_deltas() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    normalized = derive_normalized_sandbox_policy(
        permission_state=permission_state,
        additional_permissions=AdditionalSandboxPermissions(
            network_access="enabled",
            extra_write_roots=("/tmp/outside.txt",),
        ),
    )

    assert normalized == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(
            access="full_access",
            extra_write_roots=("/tmp/outside.txt",),
        ),
        network=NetworkSandboxPolicy(access="enabled"),
        execution_isolation="sandboxed",
    )


def test_requested_capabilities_follow_normalized_policy() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    requested = derive_requested_capabilities(
        permission_state=permission_state,
        additional_permissions=AdditionalSandboxPermissions(
            network_access="enabled"
        ),
    )

    assert requested == EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="enabled",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
    )
