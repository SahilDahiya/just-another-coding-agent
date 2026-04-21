from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    EffectiveCapabilities,
    FileChangeApprovalRequest,
    FileSystemSandboxPolicy,
    NormalizedSandboxPolicy,
    PermissionGrantApprovalRequest,
    PermissionGrantScope,
    PermissionState,
    SandboxPermissionGrant,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
    normalize_approval_decision,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools._policy_engine import (
    PermissionAction,
    evaluate_permission_actions,
)
from just_another_coding_agent.tools._workspace import (
    canonicalize_path_target,
    path_is_within_workspace,
    resolve_workspace_path,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps


class ToolExecutionContext(Protocol):
    deps: WorkspaceDeps


_NETWORK_COMMANDS = frozenset(
    {
        "curl",
        "dig",
        "gh",
        "host",
        "nc",
        "nslookup",
        "ping",
        "scp",
        "sftp",
        "ssh",
        "telnet",
        "wget",
    }
)
_NO_NETWORK_CATCH_ALL_COMMANDS = frozenset(
    {
        "awk",
        "cat",
        "code",
        "cut",
        "echo",
        "egrep",
        "fgrep",
        "grep",
        "head",
        "jq",
        "less",
        "more",
        "nano",
        "rg",
        "ripgrep",
        "sed",
        "sort",
        "subl",
        "tail",
        "tee",
        "tr",
        "uniq",
        "vim",
        "wc",
        "yq",
    }
)
_NETWORK_PACKAGE_MANAGER_SUBCOMMANDS = {
    "npm": frozenset({"install", "i", "ci", "add", "update", "publish"}),
    "pnpm": frozenset({"install", "add", "update", "up", "create", "dlx"}),
    "yarn": frozenset({"install", "add", "up", "upgrade", "dlx", "create"}),
    "bun": frozenset({"install", "add", "update", "x", "create"}),
    "pip": frozenset({"install", "download", "wheel"}),
    "pip3": frozenset({"install", "download", "wheel"}),
    "poetry": frozenset({"install", "add", "update", "publish"}),
    "cargo": frozenset({"install", "search", "publish", "add"}),
    "go": frozenset({"get"}),
    "uv": frozenset({"sync", "lock", "add", "remove", "publish", "runx", "x"}),
}
_GIT_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "ls-remote", "pull", "push"})
_SHELL_WRAPPERS = frozenset({"bash", "sh", "dash", "zsh", "env", "sudo", "timeout"})
_SHELL_FILESYSTEM_READ_COMMANDS = frozenset(
    {
        "cat",
        "grep",
        "head",
        "ls",
        "rg",
        "ripgrep",
        "sed",
        "tail",
    }
)
_SHELL_FILESYSTEM_WRITE_COMMANDS = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "dd",
        "install",
        "ln",
        "mkdir",
        "mktemp",
        "mv",
        "rm",
        "rmdir",
        "tee",
        "touch",
        "truncate",
        "unzip",
        "zip",
    }
)
_SHELL_SOURCE_AND_DESTINATION_WRITE_COMMANDS = frozenset({"cp", "install", "ln", "mv"})
_WRITE_REDIRECTION_TOKENS = frozenset({">", ">>", "1>", "1>>", "2>", "2>>"})
_READ_REDIRECTION_TOKENS = frozenset({"<", "0<", "<<", "<<<"})
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_DD_PATH_KEYS = frozenset({"if", "of"})


@dataclass(frozen=True)
class SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None
    requested_capabilities: EffectiveCapabilities
    normalized_policy: NormalizedSandboxPolicy
    approval_required: bool


FileAccessKind = Literal["read", "write"]


def describe_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
) -> str:
    if permissions is None:
        return ""
    segments: list[str] = []
    if permissions.network_access == "enabled":
        segments.append("network enabled")
    if permissions.extra_read_roots:
        joined = ", ".join(permissions.extra_read_roots)
        segments.append(f"read-only roots: {joined}")
    if permissions.extra_write_roots:
        joined = ", ".join(permissions.extra_write_roots)
        segments.append(f"writable roots: {joined}")
    return "; ".join(segments)


def describe_shell_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
) -> str:
    if permissions is None:
        return ""
    segments: list[str] = []
    if permissions.network_access == "enabled":
        segments.append("network enabled")
    if permissions.extra_read_roots:
        joined = ", ".join(permissions.extra_read_roots)
        segments.append(f"read-only roots: {joined}")
    if permissions.extra_write_roots:
        joined = ", ".join(permissions.extra_write_roots)
        segments.append(f"outside-workspace writes: {joined}")
    return "; ".join(segments)


def _shell_command_requests_network_access(
    *,
    command: str,
    shell_family: ShellFamily,
) -> bool:
    if shell_family != "posix":
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    return _tokens_request_network_access(tokens)


def _tokens_request_network_access(tokens: list[str], *, _depth: int = 0) -> bool:
    if not tokens or _depth > 4:
        return False

    executable = tokens[0]
    if executable in _SHELL_WRAPPERS:
        unwrapped = _unwrap_shell_wrapper(tokens)
        if unwrapped is not None:
            return _tokens_request_network_access(unwrapped, _depth=_depth + 1)
    if executable in _NETWORK_COMMANDS:
        return True
    if executable == "git" and len(tokens) > 1:
        return tokens[1] in _GIT_NETWORK_SUBCOMMANDS
    if executable in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS and len(tokens) > 1:
        return tokens[1] in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS[executable]
    if executable in {"python", "python3", "python3.12"}:
        return _python_command_requests_network(tokens[1:])
    if executable in _NO_NETWORK_CATCH_ALL_COMMANDS:
        return False
    return any(_token_looks_like_network_target(token) for token in tokens[1:])


def _unwrap_shell_wrapper(tokens: list[str]) -> list[str] | None:
    executable = tokens[0]
    if executable == "env":
        index = 1
        while index < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[index]):
            index += 1
        return tokens[index:] if index < len(tokens) else None
    if executable == "sudo":
        index = 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
        return tokens[index:] if index < len(tokens) else None
    if executable == "timeout":
        index = 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
        if index < len(tokens):
            index += 1
        return tokens[index:] if index < len(tokens) else None
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-c":
            if index + 1 >= len(tokens):
                return None
            try:
                return shlex.split(tokens[index + 1], posix=True)
            except ValueError:
                return None
        if token.startswith("-") and "c" in token[1:]:
            if index + 1 >= len(tokens):
                return None
            try:
                return shlex.split(tokens[index + 1], posix=True)
            except ValueError:
                return None
    return tokens[1:] if len(tokens) > 1 else None


def _python_command_requests_network(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] == "-m" and len(tokens) > 2:
        module = tokens[1]
        if module in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS:
            return tokens[2] in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS[module]
    return False


def _token_looks_like_network_target(token: str) -> bool:
    lower_token = token.lower()
    return (
        "://" in lower_token
        or lower_token.startswith("git@")
        or lower_token.startswith("ssh://")
        or "github.com/" in lower_token
        or "gitlab.com/" in lower_token
    )


def extract_shell_permission_actions(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path,
    permission_memory,
) -> tuple[PermissionAction, ...]:
    actions: list[PermissionAction] = []

    if _shell_command_requests_network_access(
        command=command,
        shell_family=shell_family,
    ):
        actions.append(
            PermissionAction(
                action_kind="network_access",
                source="shell",
                covered_by_current_permissions=(
                    permission_state.effective_capabilities.network_access == "enabled"
                ),
                extracted_by="shell_network_heuristics",
            )
        )

    actions.extend(
        _shell_command_read_actions(
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            command=command,
            shell_family=shell_family,
        )
    )

    actions.extend(
        _shell_command_write_actions(
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            command=command,
            shell_family=shell_family,
        )
    )
    return tuple(actions)

def _shell_command_write_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    command: str,
    shell_family: ShellFamily,
) -> tuple[PermissionAction, ...]:
    if shell_family != "posix":
        return ()
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return ()
    if not tokens:
        return ()
    return _tokens_write_actions(
        permission_state=permission_state,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
        tokens=tokens,
    )


def _tokens_write_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    tokens: list[str],
    _depth: int = 0,
) -> tuple[PermissionAction, ...]:
    if not tokens or _depth > 4:
        return ()
    executable = tokens[0]
    if executable in _SHELL_WRAPPERS:
        unwrapped = _unwrap_shell_wrapper(tokens)
        if unwrapped is not None:
            return _tokens_write_actions(
                permission_state=permission_state,
                workspace_root=workspace_root,
                permission_memory=permission_memory,
                tokens=unwrapped,
                _depth=_depth + 1,
            )

    actions: list[PermissionAction] = []
    seen_targets: set[tuple[str, str]] = set()
    write_command = executable in _SHELL_FILESYSTEM_WRITE_COMMANDS
    destination_write_index = _last_positional_path_index(tokens)
    if executable not in _SHELL_SOURCE_AND_DESTINATION_WRITE_COMMANDS:
        destination_write_index = None

    for index, token in enumerate(tokens[1:], start=1):
        previous = tokens[index - 1]
        path_token: str | None = None
        write_requested = False

        if executable == "dd":
            dd_path = _dd_path_candidate_from_shell_token(token)
            if dd_path is not None:
                dd_key, path_token = dd_path
                write_requested = dd_key == "of"
        else:
            path_token = _path_candidate_from_shell_token(token)
            if path_token is not None:
                if previous in _WRITE_REDIRECTION_TOKENS:
                    write_requested = True
                elif previous in _READ_REDIRECTION_TOKENS:
                    write_requested = False
                elif destination_write_index is not None:
                    write_requested = index == destination_write_index
                else:
                    write_requested = (
                        write_command and previous not in _READ_REDIRECTION_TOKENS
                    )

        if path_token is None or not write_requested:
            continue

        resolved = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path_token,
        )
        if path_is_within_workspace(
            workspace_root=workspace_root,
            resolved_path=resolved,
        ):
            key = ("workspace", str(workspace_root.resolve()))
            if key in seen_targets:
                continue
            seen_targets.add(key)
            actions.append(
                PermissionAction(
                    action_kind="filesystem_write",
                    source="shell",
                    path_scope="workspace",
                    root=str(workspace_root.resolve()),
                    covered_by_current_permissions=permission_state.effective_capabilities.filesystem_access
                    in {"workspace_write", "full_access"},
                    extracted_by="shell_write_heuristics",
                )
            )
            continue

        scope_root = _approval_scope_root(resolved)
        key = ("non_workspace", scope_root)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        actions.append(
            PermissionAction(
                action_kind="filesystem_write",
                source="shell",
                path_scope="non_workspace",
                root=scope_root,
                covered_by_current_permissions=(
                    permission_state.effective_capabilities.filesystem_access
                    == "full_access"
                    or permission_memory.allows_write_path(resolved)
                ),
                extracted_by="shell_write_heuristics",
            )
        )

    return tuple(actions)


def _shell_command_read_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    command: str,
    shell_family: ShellFamily,
) -> tuple[PermissionAction, ...]:
    if shell_family != "posix":
        return ()
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return ()
    if not tokens:
        return ()
    return _tokens_read_actions(
        permission_state=permission_state,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
        tokens=tokens,
    )


def _tokens_read_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    tokens: list[str],
    _depth: int = 0,
) -> tuple[PermissionAction, ...]:
    if not tokens or _depth > 4:
        return ()
    executable = tokens[0]
    if executable in _SHELL_WRAPPERS:
        unwrapped = _unwrap_shell_wrapper(tokens)
        if unwrapped is not None:
            return _tokens_read_actions(
                permission_state=permission_state,
                workspace_root=workspace_root,
                permission_memory=permission_memory,
                tokens=unwrapped,
                _depth=_depth + 1,
            )
    if executable not in _SHELL_FILESYSTEM_READ_COMMANDS:
        return ()

    actions: list[PermissionAction] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, token in enumerate(tokens[1:], start=1):
        previous = tokens[index - 1]
        path_token = _path_candidate_from_shell_token(token)
        if path_token is None:
            continue
        if (
            previous in _WRITE_REDIRECTION_TOKENS
            or previous in _READ_REDIRECTION_TOKENS
        ):
            continue

        resolved = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path_token,
        )
        if path_is_within_workspace(
            workspace_root=workspace_root,
            resolved_path=resolved,
        ):
            key = ("workspace", str(workspace_root.resolve()))
            if key in seen_targets:
                continue
            seen_targets.add(key)
            actions.append(
                PermissionAction(
                    action_kind="filesystem_read",
                    source="shell",
                    path_scope="workspace",
                    root=str(workspace_root.resolve()),
                    covered_by_current_permissions=True,
                    extracted_by="shell_read_heuristics",
                )
            )
            continue

        scope_root = _approval_scope_root(resolved)
        key = ("non_workspace", scope_root)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        actions.append(
            PermissionAction(
                action_kind="filesystem_read",
                source="shell",
                path_scope="non_workspace",
                root=scope_root,
                covered_by_current_permissions=(
                    permission_state.effective_capabilities.filesystem_access
                    == "full_access"
                    or permission_memory.allows_read_path(resolved)
                ),
                extracted_by="shell_read_heuristics",
            )
        )

    return tuple(actions)


def _last_positional_path_index(tokens: list[str]) -> int | None:
    last_index: int | None = None
    for index, token in enumerate(tokens[1:], start=1):
        previous = tokens[index - 1]
        if (
            previous in _WRITE_REDIRECTION_TOKENS
            or previous in _READ_REDIRECTION_TOKENS
        ):
            continue
        if _path_candidate_from_shell_token(token) is None:
            continue
        last_index = index
    return last_index


def _dd_path_candidate_from_shell_token(token: str) -> tuple[str, str] | None:
    if "=" not in token:
        return None
    key, candidate = token.split("=", 1)
    if key not in _DD_PATH_KEYS or not candidate:
        return None
    if _token_looks_like_network_target(candidate):
        return None
    return key, candidate


def _path_candidate_from_shell_token(token: str) -> str | None:
    candidate = token
    if token.startswith("-"):
        if "=" not in token:
            return None
        _flag, candidate = token.split("=", 1)
    if not candidate:
        return None
    if _token_looks_like_network_target(candidate):
        return None
    if candidate.startswith("~"):
        return candidate
    if candidate.startswith("/"):
        return candidate
    if candidate.startswith("./") or candidate.startswith("../"):
        return candidate
    if "/" in candidate:
        return candidate
    return None


def derive_sandbox_execution_plan(
    *,
    permission_state: PermissionState,
    effective_permissions: AdditionalSandboxPermissions | None = None,
    approval_permissions: AdditionalSandboxPermissions | None = None,
) -> SandboxExecutionPlan:
    approval_required = permission_state.approval_policy.mode == "always" or (
        permission_state.approval_policy.mode == "on_escalation"
        and approval_permissions is not None
    )
    normalized_permissions = (
        effective_permissions if effective_permissions is not None else None
    )
    return SandboxExecutionPlan(
        requested_permissions=approval_permissions,
        requested_capabilities=derive_requested_capabilities(
            permission_state=permission_state,
            additional_permissions=(
                effective_permissions
                if effective_permissions is not None
                else approval_permissions
            ),
        ),
        normalized_policy=derive_normalized_sandbox_policy(
            permission_state=permission_state,
            additional_permissions=normalized_permissions,
        ),
        approval_required=approval_required,
    )


def _approval_scope_root(resolved_path: Path) -> str:
    canonical_path = canonicalize_path_target(resolved_path)
    if canonical_path.exists() and canonical_path.is_dir():
        return str(canonical_path)
    parent = canonical_path.parent
    if parent.exists() and parent != parent.parent:
        return str(parent.resolve())
    return str(canonical_path)


def build_permission_grants(
    *,
    permissions: AdditionalSandboxPermissions | None,
    network_scope: PermissionGrantScope = "once",
    filesystem_scope: PermissionGrantScope = "session",
) -> tuple[SandboxPermissionGrant, ...]:
    if permissions is None:
        return ()
    grants: list[SandboxPermissionGrant] = []
    if permissions.network_access is not None:
        grants.append(
            SandboxPermissionGrant(
                permissions=AdditionalSandboxPermissions(
                    network_access=permissions.network_access,
                ),
                scope=network_scope,
            )
        )
    if permissions.extra_read_roots or permissions.extra_write_roots:
        grants.append(
            SandboxPermissionGrant(
                permissions=AdditionalSandboxPermissions(
                    extra_read_roots=permissions.extra_read_roots,
                    extra_write_roots=permissions.extra_write_roots,
                ),
                scope=filesystem_scope,
            )
        )
    return tuple(grants)


def plan_shell_execution(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path | None = None,
    permission_memory=None,
) -> SandboxExecutionPlan:
    approval_network_access = (
        "enabled"
        if _shell_command_requests_network_access(
            command=command,
            shell_family=shell_family,
        )
        and permission_state.effective_capabilities.network_access != "enabled"
        else None
    )
    approval_read_roots: tuple[str, ...] = ()
    approval_write_roots: tuple[str, ...] = ()
    if workspace_root is not None and permission_memory is not None:
        actions = extract_shell_permission_actions(
            permission_state=permission_state,
            command=command,
            shell_family=shell_family,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
        )
        evaluations = evaluate_permission_actions(actions=actions)
        if any(
            evaluation.action.action_kind == "network_access"
            and evaluation.match.decision == "prompt"
            for evaluation in evaluations
        ):
            approval_network_access = "enabled"
        approval_read_roots = tuple(
            evaluation.action.root
            for evaluation in evaluations
            if evaluation.action.action_kind == "filesystem_read"
            and evaluation.action.path_scope == "non_workspace"
            and evaluation.match.decision == "prompt"
            and evaluation.action.root is not None
        )
        approval_write_roots = tuple(
            evaluation.action.root
            for evaluation in evaluations
            if evaluation.action.action_kind == "filesystem_write"
            and evaluation.action.path_scope == "non_workspace"
            and evaluation.match.decision == "prompt"
            and evaluation.action.root is not None
        )

    approval_permissions: AdditionalSandboxPermissions | None = None
    if (
        approval_network_access is not None
        or approval_read_roots
        or approval_write_roots
    ):
        approval_permissions = AdditionalSandboxPermissions(
            network_access=approval_network_access,
            extra_read_roots=approval_read_roots,
            extra_write_roots=approval_write_roots,
        )

    return derive_sandbox_execution_plan(
        permission_state=permission_state,
        effective_permissions=None,
        approval_permissions=approval_permissions,
    )


async def approved_read_only_filesystem_policy(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
) -> FileSystemSandboxPolicy:
    plan = await _approved_file_access_plan(
        ctx=ctx,
        tool_path=tool_path,
        action=action,
        access_kind="read",
    )
    return plan.normalized_policy.filesystem


async def maybe_request_file_write_approval(
    *,
    ctx: ToolExecutionContext,
    tool_path: str,
    action: str,
) -> None:
    await _approved_file_access_plan(
        ctx=ctx,
        tool_path=tool_path,
        action=action,
        access_kind="write",
    )


async def _approved_file_access_plan(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
    access_kind: FileAccessKind,
) -> SandboxExecutionPlan:
    permission_state = ctx.deps.permission_state
    effective_permissions: AdditionalSandboxPermissions | None = None
    approval_permissions: AdditionalSandboxPermissions | None = None
    outside_workspace = False
    approval_scope_root: str | None = None

    if tool_path is not None:
        resolved = resolve_workspace_path(
            workspace_root=ctx.deps.workspace_root,
            tool_path=tool_path,
        )
        outside_workspace = not path_is_within_workspace(
            workspace_root=ctx.deps.workspace_root,
            resolved_path=resolved,
        )
        if (
            outside_workspace
            and permission_state.effective_capabilities.filesystem_access
            != "full_access"
        ):
            approval_scope_root = _approval_scope_root(resolved)
            if access_kind == "read":
                effective_permissions = AdditionalSandboxPermissions(
                    extra_read_roots=(approval_scope_root,),
                )
                if not ctx.deps.permission_memory.allows_read_path(resolved):
                    approval_permissions = effective_permissions
            else:
                effective_permissions = AdditionalSandboxPermissions(
                    extra_write_roots=(approval_scope_root,),
                )
                if not ctx.deps.permission_memory.allows_write_path(resolved):
                    approval_permissions = effective_permissions

    plan = derive_sandbox_execution_plan(
        permission_state=permission_state,
        effective_permissions=effective_permissions,
        approval_permissions=approval_permissions,
    )
    if not plan.approval_required:
        return plan
    if ctx.deps.approval_requester is None:
        raise RuntimeError(
            f"{action.capitalize()} requires approval, but no approval "
            "requester is configured"
        )

    reason_prefix = (
        f"allow {action} outside workspace"
        if outside_workspace
        else f"allow {action}"
    )
    permission_detail = describe_permission_delta(plan.requested_permissions)
    reason = f"{reason_prefix}: {truncate_activity_label(tool_path)}"
    if permission_detail:
        reason = f"{reason} ({permission_detail})"
    if access_kind == "read":
        request = PermissionGrantApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            request_kind="permission_grant",
            reason=reason,
            grant_kind="filesystem_read",
            target=approval_scope_root,
            requested_capabilities=plan.requested_capabilities,
            requested_permissions=plan.requested_permissions,
            requested_grants=build_permission_grants(
                permissions=plan.requested_permissions,
                filesystem_scope="session",
            ),
        )
    else:
        request = FileChangeApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            request_kind="file_change",
            reason=reason,
            path=tool_path or "",
            change_kind=action if action in {"write", "edit"} else "write",
            requested_capabilities=plan.requested_capabilities,
            requested_permissions=plan.requested_permissions,
            requested_grants=build_permission_grants(
                permissions=plan.requested_permissions,
                filesystem_scope="session",
            ),
        )
    decision = normalize_approval_decision(
        request=request,
        decision=await ctx.deps.approval_requester(request),
    )
    if decision.decision != "approved":
        raise RuntimeError(
            f"{action.capitalize()} approval did not return an approved decision"
        )
    remember_approved_grants(
        permission_memory=ctx.deps.permission_memory,
        grants=decision.granted_grants,
    )
    return plan


def remember_approved_permissions(
    *,
    permission_memory,
    permissions: AdditionalSandboxPermissions,
) -> None:
    for root in permissions.extra_read_roots:
        permission_memory.remember_read_root(root)
    for root in permissions.extra_write_roots:
        permission_memory.remember_write_root(root)


def remember_approved_grants(
    *,
    permission_memory,
    grants: tuple[SandboxPermissionGrant, ...],
) -> None:
    for grant in grants:
        if grant.scope != "session":
            continue
        remember_approved_permissions(
            permission_memory=permission_memory,
            permissions=grant.permissions,
        )


__all__ = [
    "approved_read_only_filesystem_policy",
    "SandboxExecutionPlan",
    "describe_permission_delta",
    "describe_shell_permission_delta",
    "derive_sandbox_execution_plan",
    "extract_shell_permission_actions",
    "maybe_request_file_write_approval",
    "plan_shell_execution",
    "build_permission_grants",
    "remember_approved_grants",
    "remember_approved_permissions",
]
