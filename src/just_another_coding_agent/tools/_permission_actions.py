from __future__ import annotations

from pathlib import Path
from typing import Literal

from just_another_coding_agent.contracts.sandbox import PermissionState
from just_another_coding_agent.tools._policy_engine import (
    ActionSource,
    PermissionAction,
)
from just_another_coding_agent.tools._workspace import (
    canonicalize_path_target,
    path_is_within_workspace,
    resolve_workspace_path,
)


def approval_scope_root(resolved_path: Path) -> str:
    canonical_path = canonicalize_path_target(resolved_path)
    if canonical_path.exists() and canonical_path.is_dir():
        return str(canonical_path)
    parent = canonical_path.parent
    if parent.exists() and parent != parent.parent:
        return str(parent.resolve())
    return str(canonical_path)


def filesystem_path_permission_action(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    tool_path: str,
    action_kind: Literal["filesystem_read", "filesystem_write"],
    source: ActionSource,
    extracted_by: str,
    workspace_write_covered_by_current_permissions: bool = True,
) -> PermissionAction:
    resolved = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=tool_path,
    )
    outside_workspace = not path_is_within_workspace(
        workspace_root=workspace_root,
        resolved_path=resolved,
    )
    path_scope: Literal["workspace", "non_workspace"] = (
        "non_workspace" if outside_workspace else "workspace"
    )

    if action_kind == "filesystem_read":
        covered_by_current_permissions = (
            permission_state.effective_capabilities.filesystem_access
            == "full_access"
            or not outside_workspace
            or permission_memory.allows_read_path(resolved)
        )
    elif outside_workspace:
        covered_by_current_permissions = (
            permission_state.effective_capabilities.filesystem_access
            == "full_access"
            or permission_memory.allows_write_path(resolved)
        )
    else:
        covered_by_current_permissions = workspace_write_covered_by_current_permissions

    return PermissionAction(
        action_kind=action_kind,
        source=source,
        path_scope=path_scope,
        root=(
            approval_scope_root(resolved)
            if outside_workspace
            else str(workspace_root.resolve())
        ),
        covered_by_current_permissions=covered_by_current_permissions,
        extracted_by=extracted_by,
    )


__all__ = [
    "approval_scope_root",
    "filesystem_path_permission_action",
]
