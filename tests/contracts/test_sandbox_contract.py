from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalOption,
    ApprovalPolicy,
    ApprovalRequest,
    CommandExecutionApprovalRequest,
    DangerFullAccessSandboxPolicy,
    EffectiveCapabilities,
    ExternalSandboxPolicy,
    FileChangeApprovalRequest,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    NormalizedSandboxPolicy,
    PermissionGrantApprovalRequest,
    ReadOnlySandboxPolicy,
    SandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
    derive_effective_capabilities,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
    normalize_approval_decision,
)
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan
from just_another_coding_agent.tools._permissions import (
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


def test_approval_policy_accepts_request_kind_overrides() -> None:
    policy = ApprovalPolicy(
        mode="on_escalation",
        by_kind={
            "file_change": "always",
            "permission_grant": "never",
        },
    )

    assert policy.mode == "on_escalation"
    assert policy.by_kind == {
        "file_change": "always",
        "permission_grant": "never",
    }


def test_approval_policy_rejects_empty_explicit_request_kind_overrides() -> None:
    with pytest.raises(ValidationError, match="by_kind must not be empty"):
        ApprovalPolicy(mode="on_escalation", by_kind={})


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
        approval_by_kind={},
    )

    assert derive_effective_capabilities(
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    ) == EffectiveCapabilities(
        filesystem_access="full_access",
        network_access="enabled",
        execution_isolation="unsandboxed",
        approval_mode="never",
        approval_by_kind={},
    )


def test_derive_effective_capabilities_carries_request_kind_overrides() -> None:
    capabilities = derive_effective_capabilities(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(
            mode="on_escalation",
            by_kind={"file_change": "always"},
        ),
    )

    assert capabilities == EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
        approval_by_kind={"file_change": "always"},
    )


def test_approval_request_and_decision_are_strict_contract_models() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="full_access",
        network_access="enabled",
        execution_isolation="sandboxed",
        approval_mode="on_escalation",
    )

    request = CommandExecutionApprovalRequest(
        request_id="approval-1",
        request_kind="command_execution",
        reason="Enable network for a package install.",
        command="curl https://example.com",
        cwd="/workspace",
        shell_family="posix",
        requested_capabilities=capabilities,
    )
    assert request.requested_capabilities == capabilities

    adapter = TypeAdapter(ApprovalRequest)
    assert adapter.validate_python(
        request.model_dump(mode="json")
    ) == request
    assert adapter.validate_python(
        FileChangeApprovalRequest(
            request_id="approval-2",
            request_kind="file_change",
            reason="Allow edit outside workspace.",
            path="../outside.txt",
            change_kind="edit",
            requested_capabilities=capabilities,
        ).model_dump(mode="json")
    ) == FileChangeApprovalRequest(
        request_id="approval-2",
        request_kind="file_change",
        reason="Allow edit outside workspace.",
        path="../outside.txt",
        change_kind="edit",
        requested_capabilities=capabilities,
    )
    assert adapter.validate_python(
        PermissionGrantApprovalRequest(
            request_id="approval-3",
            request_kind="permission_grant",
            reason="Grant read access outside workspace.",
            grant_kind="filesystem_read",
            target="/tmp",
            requested_capabilities=capabilities,
            requested_permissions=AdditionalSandboxPermissions(
                extra_read_roots=("/tmp",),
            ),
            requested_grants=(
                {
                    "permissions": {
                        "extra_read_roots": ["/tmp"],
                    },
                    "scope": "session",
                },
            ),
        ).model_dump(mode="json")
    ) == PermissionGrantApprovalRequest(
        request_id="approval-3",
        request_kind="permission_grant",
        reason="Grant read access outside workspace.",
        grant_kind="filesystem_read",
        target="/tmp",
        requested_capabilities=capabilities,
        requested_permissions=AdditionalSandboxPermissions(
            extra_read_roots=("/tmp",),
        ),
        requested_grants=(
            {
                "permissions": {
                    "extra_read_roots": ["/tmp"],
                },
                "scope": "session",
            },
        ),
    )

    decision = ApprovalDecision(
        request_id="approval-1",
        decision="approved",
    )
    assert decision.decision == "approved"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommandExecutionApprovalRequest(
            request_id="approval-2",
            request_kind="command_execution",
            reason="bad",
            command="pwd",
            cwd="/workspace",
            shell_family="posix",
            requested_capabilities=capabilities,
            extra_field=True,
        )


def test_additional_sandbox_permissions_require_a_non_empty_delta() -> None:
    with pytest.raises(ValidationError, match="must request at least one"):
        AdditionalSandboxPermissions()


def test_approval_request_rejects_requested_permissions_without_requested_grants() -> None:  # noqa: E501
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )

    with pytest.raises(ValidationError, match="requested_grants"):
        PermissionGrantApprovalRequest(
            request_id="approval-3",
            request_kind="permission_grant",
            reason="Grant read access outside workspace.",
            grant_kind="filesystem_read",
            target="/tmp",
            requested_capabilities=capabilities,
            requested_permissions=AdditionalSandboxPermissions(
                extra_read_roots=("/tmp",),
            ),
        )


def test_approval_request_rejects_removed_turn_grant_scope() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )

    with pytest.raises(ValidationError, match="once|session"):
        PermissionGrantApprovalRequest(
            request_id="approval-3",
            request_kind="permission_grant",
            reason="Grant read access outside workspace.",
            grant_kind="filesystem_read",
            target="/tmp",
            requested_capabilities=capabilities,
            requested_permissions=AdditionalSandboxPermissions(
                extra_read_roots=("/tmp",),
            ),
            requested_grants=(
                {
                    "permissions": {
                        "extra_read_roots": ["/tmp"],
                    },
                    "scope": "turn",
                },
            ),
        )


def test_normalize_approval_decision_defaults_to_requested_permissions_and_scope() -> None:  # noqa: E501
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )
    request = PermissionGrantApprovalRequest(
        request_id="approval-3",
        request_kind="permission_grant",
        reason="Grant read access outside workspace.",
        grant_kind="filesystem_read",
        target="/tmp",
        requested_capabilities=capabilities,
        requested_permissions=AdditionalSandboxPermissions(
            extra_read_roots=("/tmp",),
        ),
        requested_grants=(
            {
                "permissions": {
                    "extra_read_roots": ["/tmp"],
                },
                "scope": "session",
            },
        ),
    )

    decision = normalize_approval_decision(
        request=request,
        decision=ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        ),
    )

    assert decision == ApprovalDecision(
        request_id=request.request_id,
        decision="approved",
        granted_permissions=AdditionalSandboxPermissions(
            extra_read_roots=("/tmp",),
        ),
        granted_grants=(
            {
                "permissions": {
                    "extra_read_roots": ["/tmp"],
                },
                "scope": "session",
            },
        ),
    )


def test_normalize_approval_decision_rejects_grants_that_do_not_match_request() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )
    request = CommandExecutionApprovalRequest(
        request_id="approval-1",
        request_kind="command_execution",
        reason="Enable network for a package install.",
        command="curl https://example.com",
        cwd="/workspace",
        shell_family="posix",
        requested_capabilities=capabilities.model_copy(
            update={"network_access": "enabled"}
        ),
        requested_permissions=AdditionalSandboxPermissions(
            network_access="enabled",
        ),
        requested_grants=(
            {
                "permissions": {
                    "network_access": "enabled",
                },
                "scope": "once",
            },
        ),
    )

    with pytest.raises(ValueError, match="must match requested_grants"):
        normalize_approval_decision(
            request=request,
            decision=ApprovalDecision(
                request_id=request.request_id,
                decision="approved",
                granted_permissions=AdditionalSandboxPermissions(
                    network_access="enabled",
                ),
                granted_grants=(
                    {
                        "permissions": {
                            "network_access": "enabled",
                        },
                        "scope": "session",
                    },
                ),
            ),
        )


def test_normalize_approval_decision_defaults_to_requested_grants() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )
    request = CommandExecutionApprovalRequest(
        request_id="approval-1",
        request_kind="command_execution",
        reason="allow shell command: curl https://example.com (network enabled)",
        display_subject="curl https://example.com",
        command="curl https://example.com",
        cwd="/workspace",
        shell_family="posix",
        requested_capabilities=capabilities,
        requested_permissions=AdditionalSandboxPermissions(network_access="enabled"),
        requested_grants=(
            {
                "permissions": {"network_access": "enabled"},
                "scope": "once",
            },
        ),
        options=(
            ApprovalOption(
                option_id="allow-once",
                label="Allow once",
                decision="approved",
                granted_permissions=AdditionalSandboxPermissions(
                    network_access="enabled"
                ),
                granted_grants=(
                    {
                        "permissions": {"network_access": "enabled"},
                        "scope": "once",
                    },
                ),
            ),
            ApprovalOption(
                option_id="allow-session",
                label="Allow curl for this session",
                decision="approved",
                granted_permissions=AdditionalSandboxPermissions(
                    network_access="enabled"
                ),
                granted_grants=(
                    {
                        "permissions": {"network_access": "enabled"},
                        "scope": "session",
                        "command_prefix": ["curl"],
                    },
                ),
            ),
            ApprovalOption(
                option_id="deny",
                label="Deny",
                decision="denied",
            ),
        ),
    )

    decision = normalize_approval_decision(
        request=request,
        decision=ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        ),
    )

    assert decision.option_id is None
    assert decision.granted_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert len(decision.granted_grants) == 1
    assert decision.granted_grants[0].scope == "once"


def test_normalize_approval_decision_accepts_explicit_option_id() -> None:
    capabilities = EffectiveCapabilities(
        filesystem_access="workspace_write",
        network_access="restricted",
        execution_isolation="unsandboxed",
        approval_mode="on_escalation",
    )
    request = CommandExecutionApprovalRequest(
        request_id="approval-1",
        request_kind="command_execution",
        reason="allow shell command: curl https://example.com (network enabled)",
        display_subject="curl https://example.com",
        command="curl https://example.com",
        cwd="/workspace",
        shell_family="posix",
        requested_capabilities=capabilities,
        requested_permissions=AdditionalSandboxPermissions(network_access="enabled"),
        requested_grants=(
            {
                "permissions": {"network_access": "enabled"},
                "scope": "once",
            },
        ),
        options=(
            ApprovalOption(
                option_id="allow-once",
                label="Allow once",
                decision="approved",
                granted_permissions=AdditionalSandboxPermissions(
                    network_access="enabled"
                ),
                granted_grants=(
                    {
                        "permissions": {"network_access": "enabled"},
                        "scope": "once",
                    },
                ),
            ),
            ApprovalOption(
                option_id="allow-session",
                label="Allow curl for this session",
                decision="approved",
                granted_permissions=AdditionalSandboxPermissions(
                    network_access="enabled"
                ),
                granted_grants=(
                    {
                        "permissions": {"network_access": "enabled"},
                        "scope": "session",
                        "command_prefix": ["curl"],
                    },
                ),
            ),
            ApprovalOption(
                option_id="deny",
                label="Deny",
                decision="denied",
            ),
        ),
    )

    decision = normalize_approval_decision(
        request=request,
        decision=ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
            option_id="allow-session",
        ),
    )

    assert decision.option_id == "allow-session"
    assert len(decision.granted_grants) == 1
    assert decision.granted_grants[0].scope == "session"
    assert decision.granted_grants[0].command_prefix == ("curl",)


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


def test_derive_sandbox_execution_plan_requires_approval_for_permission_deltas() -> None:  # noqa: E501
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    requested_permissions = AdditionalSandboxPermissions(network_access="enabled")

    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        request_kind="command_execution",
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
            approval_by_kind={},
        ),
        normalized_policy=NormalizedSandboxPolicy(
            filesystem=FileSystemSandboxPolicy(access="workspace_write"),
            network=NetworkSandboxPolicy(access="enabled"),
            execution_isolation="sandboxed",
        ),
        approval_required=True,
    )


def test_derive_sandbox_execution_plan_skips_escalation_approval_without_delta() -> None:  # noqa: E501
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        request_kind="command_execution",
    )

    assert plan.approval_required is False
    assert plan.requested_permissions is None
    assert plan.normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )


def test_derive_sandbox_execution_plan_honors_request_kind_override() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(
            mode="on_escalation",
            by_kind={"file_change": "always"},
        ),
    )

    file_change_plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        request_kind="file_change",
    )
    command_plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        request_kind="command_execution",
    )

    assert file_change_plan.approval_required is True
    assert command_plan.approval_required is False


def test_plan_shell_execution_requests_network_delta_for_explicit_network_command(
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
        command="curl https://example.com",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_wrapped_network_command(
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
        command='bash -lc "curl https://example.com"',
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_requests_network_delta_for_package_manager_command(
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
        command="python -m pip install requests",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert plan.requested_permissions == AdditionalSandboxPermissions(
        network_access="enabled"
    )
    assert plan.normalized_policy.network.access == "restricted"
    assert plan.approval_required is True


def test_plan_shell_execution_does_not_request_network_delta_for_grep_url_pattern(
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
        command="grep 'https://example.com' file.txt",
        shell_family="posix",
        workspace_root=workspace_root,
        permission_memory=permission_memory,
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


def test_plan_shell_execution_routes_every_decision_through_the_rule_engine(
    tmp_path, monkeypatch
) -> None:
    # Codification test: plan_shell_execution has exactly one decider.
    # If any code path inside plan_shell_execution decides allow/prompt/deny
    # without calling evaluate_permission_actions, this test will catch it.
    from just_another_coding_agent.tools import _permissions

    call_count = {"n": 0}
    real_evaluate = _permissions.evaluate_permission_actions

    def counting_evaluate(*args, **kwargs):
        call_count["n"] += 1
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        _permissions, "evaluate_permission_actions", counting_evaluate
    )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    permission_memory = SessionPermissionMemory()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for command in (
        "curl https://example.com",
        "grep 'needle' file.txt",
        "tee ../outside.txt",
        "cat safe.txt",
    ):
        call_count["n"] = 0
        _permissions.plan_shell_execution(
            permission_state=permission_state,
            command=command,
            shell_family="posix",
            workspace_root=workspace_root,
            permission_memory=permission_memory,
        )
        assert call_count["n"] == 1, (
            f"plan_shell_execution must consult the rule engine exactly once "
            f"per call; {command!r} triggered {call_count['n']}"
        )


def test_plan_shell_execution_rejects_missing_workspace_or_memory() -> None:
    # Codification test: workspace_root and permission_memory are required.
    # The pre-rule-engine fallback that accepted None for either argument
    # has been removed; calling without them must fail loudly rather than
    # silently bypass the rule engine.
    import pytest

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    with pytest.raises(TypeError):
        plan_shell_execution(  # type: ignore[call-arg]
            permission_state=permission_state,
            command="curl https://example.com",
            shell_family="posix",
        )
