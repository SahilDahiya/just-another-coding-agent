from __future__ import annotations

from pathlib import Path

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalPolicy,
    DangerFullAccessSandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools._permissions import (
    extract_file_permission_actions,
    extract_shell_permission_actions,
)
from just_another_coding_agent.tools._policy_engine import (
    PermissionAction,
    evaluate_permission_actions,
)
from just_another_coding_agent.tools.deps import SessionPermissionMemory


def _default_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )


def _full_access_permission_state():
    return build_permission_state(
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
    )


def test_given_default_policy_when_file_tool_non_workspace_read_is_extracted_then_non_workspace_read_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    actions = extract_file_permission_actions(
        permission_state=_default_permission_state(),
        tool_path="../outside.txt",
        action_source="read_tool",
        access_kind="read",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_read",
            source="read_tool",
            path_scope="non_workspace",
            root=str(Path(tmp_path).resolve()),
            covered_by_current_permissions=False,
            extracted_by="tool_path_resolution",
        ),
    )


def test_given_default_policy_when_file_tool_non_workspace_write_is_extracted_then_non_workspace_write_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    actions = extract_file_permission_actions(
        permission_state=_default_permission_state(),
        tool_path="../outside.txt",
        action_source="write_tool",
        access_kind="write",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_write",
            source="write_tool",
            path_scope="non_workspace",
            root=str(Path(tmp_path).resolve()),
            covered_by_current_permissions=False,
            extracted_by="tool_path_resolution",
        ),
    )


def test_given_default_policy_when_edit_tool_workspace_write_is_extracted_then_workspace_write_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    actions = extract_file_permission_actions(
        permission_state=_default_permission_state(),
        tool_path="note.txt",
        action_source="edit_tool",
        access_kind="write",
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_write",
            source="edit_tool",
            path_scope="workspace",
            root=str(workspace_root.resolve()),
            covered_by_current_permissions=True,
            extracted_by="tool_path_resolution",
        ),
    )


def test_given_default_policy_when_shell_network_command_is_extracted_then_network_access_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    actions = extract_shell_permission_actions(
        permission_state=_default_permission_state(),
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="network_access",
            source="shell",
            command_prefix=("curl",),
            covered_by_current_permissions=False,
            extracted_by="shell_network_heuristics",
        ),
    )


def test_given_default_policy_when_shell_non_workspace_write_command_is_extracted_then_non_workspace_write_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    actions = extract_shell_permission_actions(
        permission_state=_default_permission_state(),
        command=f"tee {outside_dir / 'note.txt'}",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_write",
            source="shell",
            path_scope="non_workspace",
            root=str(Path(outside_dir).resolve()),
            covered_by_current_permissions=False,
            extracted_by="shell_write_heuristics",
        ),
    )


def test_given_default_policy_when_shell_non_workspace_read_command_is_extracted_then_non_workspace_read_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    actions = extract_shell_permission_actions(
        permission_state=_default_permission_state(),
        command="cat ../outside.txt",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_read",
            source="shell",
            path_scope="non_workspace",
            root=str(Path(tmp_path).resolve()),
            covered_by_current_permissions=False,
            extracted_by="shell_read_heuristics",
        ),
    )


def test_given_default_policy_when_shell_workspace_write_command_is_extracted_then_workspace_write_action_is_returned(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    actions = extract_shell_permission_actions(
        permission_state=_default_permission_state(),
        command="tee ./note.txt",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_write",
            source="shell",
            path_scope="workspace",
            root=str(workspace_root.resolve()),
            covered_by_current_permissions=True,
            extracted_by="shell_write_heuristics",
        ),
    )


def test_given_uncovered_shell_network_action_when_rules_are_evaluated_then_prompt() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="network_access",
                source="shell",
                covered_by_current_permissions=False,
                extracted_by="shell_network_heuristics",
            ),
        ),
    )

    assert evaluations[0].match.rule_id == "prompt-shell-network-when-uncovered"
    assert evaluations[0].match.decision == "prompt"


def test_given_covered_shell_network_action_when_rules_are_evaluated_then_allow() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="network_access",
                source="shell",
                covered_by_current_permissions=True,
                extracted_by="shell_network_heuristics",
            ),
        ),
    )

    assert evaluations[0].match.rule_id == "allow-shell-network-when-covered"
    assert evaluations[0].match.decision == "allow"


def test_given_uncovered_shell_non_workspace_read_action_when_rules_are_evaluated_then_prompt() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="filesystem_read",
                source="shell",
                path_scope="non_workspace",
                root="/tmp",
                covered_by_current_permissions=False,
                extracted_by="shell_read_heuristics",
            ),
        ),
    )

    assert (
        evaluations[0].match.rule_id == "prompt-non-workspace-read-when-uncovered"
    )
    assert evaluations[0].match.decision == "prompt"


def test_given_covered_file_tool_non_workspace_read_action_when_rules_are_evaluated_then_allow() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="filesystem_read",
                source="read_tool",
                path_scope="non_workspace",
                root="/tmp",
                covered_by_current_permissions=True,
                extracted_by="tool_path_resolution",
            ),
        ),
    )

    assert evaluations[0].match.rule_id == "allow-non-workspace-read-when-covered"
    assert evaluations[0].match.decision == "allow"


def test_given_uncovered_shell_non_workspace_write_action_when_rules_are_evaluated_then_prompt() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="filesystem_write",
                source="shell",
                path_scope="non_workspace",
                root="/tmp",
                covered_by_current_permissions=False,
                extracted_by="shell_write_heuristics",
            ),
        ),
    )

    assert (
        evaluations[0].match.rule_id == "prompt-non-workspace-write-when-uncovered"
    )
    assert evaluations[0].match.decision == "prompt"


def test_given_uncovered_file_tool_non_workspace_write_action_when_rules_are_evaluated_then_prompt() -> None:
    evaluations = evaluate_permission_actions(
        actions=(
            PermissionAction(
                action_kind="filesystem_write",
                source="write_tool",
                path_scope="non_workspace",
                root="/tmp",
                covered_by_current_permissions=False,
                extracted_by="tool_path_resolution",
            ),
        ),
    )

    assert evaluations[0].match.rule_id == "prompt-non-workspace-write-when-uncovered"
    assert evaluations[0].match.decision == "prompt"


def test_given_session_approved_shell_non_workspace_read_root_when_shell_command_is_extracted_then_action_is_marked_as_covered(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    permission_memory = SessionPermissionMemory()
    permission_memory.remember_read_root(str(Path(tmp_path).resolve()))

    actions = extract_shell_permission_actions(
        permission_state=_default_permission_state(),
        command="cat ../outside.txt",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )

    assert actions == (
        PermissionAction(
            action_kind="filesystem_read",
            source="shell",
            path_scope="non_workspace",
            root=str(Path(tmp_path).resolve()),
            covered_by_current_permissions=True,
            extracted_by="shell_read_heuristics",
        ),
    )


def test_given_full_access_policy_when_shell_network_command_is_extracted_then_network_access_action_is_marked_as_covered(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    actions = extract_shell_permission_actions(
        permission_state=_full_access_permission_state(),
        command="curl https://example.com",
        shell_family=detect_default_shell_family(),
        workspace_root=workspace_root,
        permission_memory=SessionPermissionMemory(),
    )

    assert actions == (
        PermissionAction(
            action_kind="network_access",
            source="shell",
            command_prefix=("curl",),
            covered_by_current_permissions=True,
            extracted_by="shell_network_heuristics",
        ),
    )
