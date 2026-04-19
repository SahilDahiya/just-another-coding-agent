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
    WorkspaceWriteStrictSandboxPolicy,
    build_permission_state,
    derive_effective_capabilities,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
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

    workspace_write_strict = adapter.validate_python(
        {"mode": "workspace_write_strict"}
    )
    assert workspace_write_strict == WorkspaceWriteStrictSandboxPolicy()

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
        sandbox_policy=WorkspaceWriteStrictSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    ) == EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
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


def test_derive_sandbox_execution_plan_requires_approval_for_permission_deltas(
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    requested_permissions = AdditionalSandboxPermissions(
        network_access="enabled"
    )

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


def test_derive_sandbox_execution_plan_skips_escalation_approval_without_delta(
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
    )

    assert plan.approval_required is False
    assert plan.requested_permissions is None
    assert plan.normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )


def test_plan_shell_execution_requests_network_delta_for_explicit_network_command(
) -> None:
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
    assert plan.normalized_policy.network.access == "enabled"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_wrapped_network_command(
) -> None:
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
    assert plan.normalized_policy.network.access == "enabled"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_package_manager_command(
) -> None:
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
    assert plan.normalized_policy.network.access == "enabled"
    assert plan.approval_required is True


def test_plan_shell_execution_does_not_request_network_delta_for_grep_url_pattern(
) -> None:
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


def test_plan_shell_execution_does_not_request_network_delta_for_echo_url_content(
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="echo 'See https://example.com for docs' > README.md",
        shell_family="posix",
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is False


def test_plan_shell_execution_does_not_request_network_delta_for_sed_url_replacement(
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="sed -i 's|https://old|https://new|g' config.yml",
        shell_family="posix",
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is False


def test_plan_shell_execution_does_not_request_network_delta_for_python_url_string(
) -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command='python -c "print(\'https://example.com\')"',
        shell_family="posix",
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is False


def test_plan_shell_execution_keeps_local_command_in_default_sandbox() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="printf ok",
        shell_family="posix",
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )
    assert plan.approval_required is False


def test_plan_shell_execution_allows_explicit_outside_read_without_approval(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command=f"cat {outside_root / 'README.md'}",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.filesystem == FileSystemSandboxPolicy(
        access="workspace_write",
    )
    assert plan.approval_required is False


def test_plan_shell_execution_requests_outside_read_approval_in_strict_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteStrictSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command=f"cat {outside_root / 'README.md'}",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        extra_read_roots=(str(outside_root),),
    )
    assert plan.normalized_policy.filesystem == FileSystemSandboxPolicy(
        access="workspace_write",
        extra_read_roots=(str(outside_root),),
    )
    assert plan.approval_required is True


def test_plan_shell_execution_keeps_outside_read_approval_free_when_memory_exists(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    memory = SessionPermissionMemory()
    memory.remember_read_root(str(outside_root))

    plan = plan_shell_execution(
        permission_state=permission_state,
        command=f'bash -lc "cat {outside_root / "README.md"}"',
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=memory,
    )

    assert plan.requested_permissions is None
    assert plan.normalized_policy.filesystem == FileSystemSandboxPolicy(
        access="workspace_write",
    )
    assert plan.approval_required is False


def test_plan_shell_execution_treats_cp_source_as_read_and_destination_as_write(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_read_root = tmp_path / "outside-read"
    outside_read_root.mkdir()
    outside_write_root = tmp_path / "outside-write"
    outside_write_root.mkdir()
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command=(
            f"cp {outside_read_root / 'source.txt'} "
            f"{outside_write_root / 'dest.txt'}"
        ),
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        extra_write_roots=(str(outside_write_root),),
    )
    assert plan.normalized_policy.filesystem == FileSystemSandboxPolicy(
        access="workspace_write",
        extra_write_roots=(str(outside_write_root),),
    )
    assert plan.approval_required is True


def test_plan_shell_execution_treats_dd_if_and_of_values_as_paths(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteStrictSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = plan_shell_execution(
        permission_state=permission_state,
        command="dd if=/etc/passwd of=/tmp/stolen",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        extra_read_roots=("/etc",),
        extra_write_roots=("/tmp",),
    )
    assert plan.normalized_policy.filesystem == FileSystemSandboxPolicy(
        access="workspace_write",
        extra_read_roots=("/etc",),
        extra_write_roots=("/tmp",),
    )
    assert plan.approval_required is True


def test_session_permission_memory_canonicalizes_symlink_roots(
    tmp_path,
) -> None:
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    alias_root = tmp_path / "outside-link"
    try:
        alias_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable in this environment: {error}")

    memory = SessionPermissionMemory()
    memory.remember_read_root(str(alias_root))
    memory.remember_write_root(str(alias_root))

    expected_root = str(outside_root.resolve())
    assert memory.approved_read_roots == {expected_root}
    assert memory.approved_write_roots == {expected_root}
    assert memory.allows_read_path(alias_root / "child.txt")
    assert memory.allows_write_path(alias_root / "child.txt")


def test_approval_scope_root_clamps_missing_root_level_target() -> None:
    assert _approval_scope_root(Path("/missing.json")) == "/missing.json"
