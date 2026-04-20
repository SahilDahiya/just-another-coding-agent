from __future__ import annotations

from pathlib import Path

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
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
    derive_effective_capabilities,
)
from just_another_coding_agent.tools._permissions import (
    SandboxExecutionPlan,
    _approval_scope_root,
    derive_sandbox_execution_plan,
    plan_shell_execution,
)
from just_another_coding_agent.tools.deps import SessionPermissionMemory


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
            access="workspace_write",
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
        additional_permissions=AdditionalSandboxPermissions(network_access="enabled"),
    )

    assert requested == EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="enabled",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
    )


def test_derive_sandbox_execution_plan_requires_approval_for_permission_deltas() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    requested_permissions = AdditionalSandboxPermissions(network_access="enabled")

    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        effective_permissions=requested_permissions,
        approval_permissions=requested_permissions,
    )

    assert plan == SandboxExecutionPlan(
        requested_permissions=requested_permissions,
        requested_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="enabled",
            execution_isolation="sandboxed",
            approval_mode="on_escalation",
        ),
        normalized_policy=NormalizedSandboxPolicy(
            filesystem=FileSystemSandboxPolicy(access="workspace_write"),
            network=NetworkSandboxPolicy(access="enabled"),
            execution_isolation="sandboxed",
        ),
        approval_required=True,
    )


def test_derive_sandbox_execution_plan_skips_escalation_approval_without_delta() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = derive_sandbox_execution_plan(permission_state=permission_state)

    assert plan.approval_required is False
    assert plan.requested_permissions is None
    assert plan.normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )


def test_plan_shell_execution_requests_network_delta_for_explicit_network_command() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="curl https://example.com",
        shell_family="posix",
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_wrapped_network_command() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command='bash -lc "curl https://example.com"',
        shell_family="posix",
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_package_manager_command() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="python -m pip install requests",
        shell_family="posix",
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_does_not_request_network_delta_for_grep_url_pattern() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="grep 'https://example.com' file.txt",
        shell_family="posix",
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is False


def test_plan_shell_execution_requests_outside_workspace_write_delta(
    tmp_path,
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    permission_memory = SessionPermissionMemory()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="tee ../outside.txt",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        extra_write_roots=(str(tmp_path.resolve()),),
    )
    assert plan.approval_required is True
    assert plan.normalized_policy.filesystem.extra_write_roots == ()


def test_plan_shell_execution_skips_remembered_outside_workspace_write_delta(
    tmp_path,
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    permission_memory = SessionPermissionMemory()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    permission_memory.remember_write_root(str(outside_dir))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    plan = plan_shell_execution(
        permission_state=permission_state,
        command=f"tee {outside_dir / 'outside.txt'}",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert plan.requested_permissions is None
    assert plan.approval_required is False


def test_approval_scope_root_prefers_existing_directory_or_parent() -> None:
    assert _approval_scope_root(Path("/tmp")) == "/tmp"
    assert _approval_scope_root(Path("/tmp/outside.txt")) == "/tmp"
