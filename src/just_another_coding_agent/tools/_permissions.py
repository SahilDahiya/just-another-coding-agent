from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    AdditionalNetworkAccess,
    AdditionalSandboxPermissions,
    ApprovalOption,
    ApprovalRequestKind,
    FileChangeApprovalRequest,
    FileSystemSandboxPolicy,
    PermissionGrantApprovalRequest,
    PermissionGrantScope,
    PermissionState,
    SandboxPermissionGrant,
    approval_mode_for_request_kind,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
    describe_approval_policy_for_request_kind,
)
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan
from just_another_coding_agent.contracts.tool_runtime import (
    ExecApprovalRequirement,
    ForbiddenApproval,
    NeedsApproval,
    SkipApproval,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools._approval_flow import (
    fulfill_approval_requirement,
)
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


@dataclass(frozen=True)
class FileAccessPlan:
    sandbox_plan: SandboxExecutionPlan
    effective_permissions: AdditionalSandboxPermissions | None
    tool_path: str | None
    action: str
    access_kind: FileAccessKind
    request_kind: ApprovalRequestKind
    approval_scope_root: str | None
    approval_policy_label: str


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


FileAccessKind = Literal["read", "write"]
FileToolActionSource = Literal["read_tool", "write_tool", "edit_tool"]


def describe_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
    *,
    write_label: str = "writable roots",
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
        segments.append(f"{write_label}: {joined}")
    return "; ".join(segments)


def describe_shell_permission_delta(
    permissions: AdditionalSandboxPermissions | None,
) -> str:
    return describe_permission_delta(
        permissions, write_label="outside-workspace writes"
    )


def _approval_denied_message(
    *,
    request: FileChangeApprovalRequest | PermissionGrantApprovalRequest,
) -> str:
    if request.request_kind == "file_change":
        return (
            f"Approval denied: {request.reason}. "
            "The file was not modified. Choose another approach or stop."
        )
    return (
        f"Approval denied: {request.reason}. "
        "The file was not read. Choose another approach or stop."
    )


def _policy_denied_message(
    *,
    request: FileChangeApprovalRequest | PermissionGrantApprovalRequest,
) -> str:
    if request.request_kind == "file_change":
        return (
            f"Approval blocked by current policy: {request.reason}. "
            "The file was not modified. Choose another approach or stop."
        )
    return (
        f"Approval blocked by current policy: {request.reason}. "
        "The file was not read. Choose another approach or stop."
    )


def _file_action_label(*, action: str, tool_path: str | None) -> str:
    if tool_path is None:
        return action
    return truncate_activity_label(tool_path)


def _file_action_subject(*, action: str, tool_path: str | None) -> str:
    if tool_path is None:
        return action
    return f"{action} {tool_path}"


def _file_access_approval_reason(file_access_plan: FileAccessPlan) -> str:
    target_label = _file_action_label(
        action=file_access_plan.action,
        tool_path=file_access_plan.tool_path,
    )
    if file_access_plan.sandbox_plan.requested_permissions is None:
        return (
            f"allow {file_access_plan.action}: {target_label} "
            f"(approval policy: {file_access_plan.approval_policy_label})"
        )

    reason = f"allow {file_access_plan.action} outside workspace: {target_label}"
    permission_detail = describe_permission_delta(
        file_access_plan.sandbox_plan.requested_permissions
    )
    if permission_detail:
        reason = f"{reason} ({permission_detail})"
    return reason


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


def _parse_posix_shell_tokens(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    return tokens


def _shell_network_command_prefix(
    command: str,
    *,
    shell_family: ShellFamily,
) -> tuple[str, ...]:
    if shell_family != "posix":
        return ()
    tokens = _parse_posix_shell_tokens(command)
    if tokens is None:
        return ()
    return _tokens_network_command_prefix(tokens)


def _tokens_network_command_prefix(
    tokens: list[str],
    *,
    _depth: int = 0,
) -> tuple[str, ...]:
    if not tokens or _depth > 4:
        return ()
    executable = tokens[0]
    if executable in _SHELL_WRAPPERS:
        unwrapped = _unwrap_shell_wrapper(tokens)
        if unwrapped is not None:
            return _tokens_network_command_prefix(unwrapped, _depth=_depth + 1)
        return ()
    if executable in _NETWORK_COMMANDS:
        return (executable,)
    if (
        executable == "git"
        and len(tokens) > 1
        and tokens[1] in _GIT_NETWORK_SUBCOMMANDS
    ):
        return ("git", tokens[1])
    if (
        executable in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS
        and len(tokens) > 1
        and tokens[1] in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS[executable]
    ):
        return (executable, tokens[1])
    return ()


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
    network_command_prefix = _shell_network_command_prefix(
        command,
        shell_family=shell_family,
    )

    if _shell_command_requests_network_access(
        command=command,
        shell_family=shell_family,
    ):
        actions.append(
            PermissionAction(
                action_kind="network_access",
                source="shell",
                command_prefix=network_command_prefix,
                covered_by_current_permissions=(
                    permission_state.effective_capabilities.network_access == "enabled"
                    or (
                        bool(network_command_prefix)
                        and permission_memory.allows_command_prefix(
                            network_command_prefix
                        )
                    )
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


def extract_file_permission_actions(
    *,
    permission_state: PermissionState,
    tool_path: str,
    action_source: FileToolActionSource,
    access_kind: FileAccessKind,
    workspace_root: Path,
    permission_memory,
) -> tuple[PermissionAction, ...]:
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

    if access_kind == "read":
        action_kind: Literal["filesystem_read", "filesystem_write"] = (
            "filesystem_read"
        )
        covered_by_current_permissions = (
            permission_state.effective_capabilities.filesystem_access
            == "full_access"
            or not outside_workspace
            or permission_memory.allows_read_path(resolved)
        )
    else:
        action_kind = "filesystem_write"
        covered_by_current_permissions = (
            permission_state.effective_capabilities.filesystem_access
            == "full_access"
            or not outside_workspace
            or permission_memory.allows_write_path(resolved)
        )

    return (
        PermissionAction(
            action_kind=action_kind,
            source=action_source,
            path_scope=path_scope,
            root=(
                _approval_scope_root(resolved)
                if outside_workspace
                else str(workspace_root.resolve())
            ),
            covered_by_current_permissions=covered_by_current_permissions,
            extracted_by="tool_path_resolution",
        ),
    )

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
    request_kind: ApprovalRequestKind,
    effective_permissions: AdditionalSandboxPermissions | None = None,
    approval_permissions: AdditionalSandboxPermissions | None = None,
) -> SandboxExecutionPlan:
    approval_mode = approval_mode_for_request_kind(
        approval_policy=permission_state.approval_policy,
        request_kind=request_kind,
    )
    if approval_mode == "always":
        approval_disposition = "prompt"
    elif approval_permissions is None:
        approval_disposition = "allowed"
    elif approval_mode == "on_escalation":
        approval_disposition = "prompt"
    else:
        approval_disposition = "denied_by_policy"
    normalized_permissions = (
        effective_permissions
        if approval_disposition == "allowed" and effective_permissions is not None
        else None
    )
    return SandboxExecutionPlan(
        requested_permissions=approval_permissions,
        requested_capabilities=derive_requested_capabilities(
            permission_state=permission_state,
            additional_permissions=(
                effective_permissions
                if approval_disposition == "allowed"
                and effective_permissions is not None
                else approval_permissions
            ),
        ),
        normalized_policy=derive_normalized_sandbox_policy(
            permission_state=permission_state,
            additional_permissions=normalized_permissions,
        ),
        approval_disposition=approval_disposition,
    )


def _approval_scope_root(resolved_path: Path) -> str:
    canonical_path = canonicalize_path_target(resolved_path)
    if canonical_path.exists() and canonical_path.is_dir():
        return str(canonical_path)
    parent = canonical_path.parent
    if parent.exists() and parent != parent.parent:
        return str(parent.resolve())
    return str(canonical_path)


def _approval_option(
    *,
    option_id: str,
    label: str,
    decision: Literal["approved", "denied"],
    granted_permissions: AdditionalSandboxPermissions | None = None,
    granted_grants: tuple[SandboxPermissionGrant, ...] = (),
) -> ApprovalOption:
    return ApprovalOption(
        option_id=option_id,
        label=label,
        decision=decision,
        granted_permissions=granted_permissions,
        granted_grants=granted_grants,
    )


def _filesystem_session_option_label(
    *,
    access_kind: Literal["read", "write"],
    root: str,
) -> str:
    verb = "reads" if access_kind == "read" else "writes"
    return f"Allow {verb} under {root} for this session"


def _shell_network_session_option_label(command_prefix: tuple[str, ...]) -> str:
    return f"Allow {' '.join(command_prefix)} for this session"


def build_permission_grants(
    *,
    permissions: AdditionalSandboxPermissions | None,
    network_scope: PermissionGrantScope = "once",
    filesystem_scope: PermissionGrantScope = "session",
    network_command_prefix: tuple[str, ...] = (),
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
                command_prefix=(
                    network_command_prefix if network_scope == "session" else ()
                ),
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


def build_permission_approval_options(
    *,
    permissions: AdditionalSandboxPermissions,
    once_label: str,
    session_label: str | None = None,
    network_command_prefix: tuple[str, ...] = (),
) -> tuple[ApprovalOption, ...]:
    options = [
        _approval_option(
            option_id="allow-once",
            label=once_label,
            decision="approved",
            granted_permissions=permissions,
            granted_grants=build_permission_grants(
                permissions=permissions,
                network_scope="once",
                filesystem_scope="once",
            ),
        )
    ]
    if session_label is not None:
        options.append(
            _approval_option(
                option_id="allow-session",
                label=session_label,
                decision="approved",
                granted_permissions=permissions,
                granted_grants=build_permission_grants(
                    permissions=permissions,
                    network_scope="session",
                    filesystem_scope="session",
                    network_command_prefix=network_command_prefix,
                ),
            )
        )
    options.append(
        _approval_option(
            option_id="deny",
            label="Deny",
            decision="denied",
        )
    )
    return tuple(options)


def build_shell_approval_options(
    *,
    command: str,
    shell_family: ShellFamily,
    permissions: AdditionalSandboxPermissions,
) -> tuple[ApprovalOption, ...]:
    session_label: str | None = None
    network_prefix = _shell_network_command_prefix(
        command,
        shell_family=shell_family,
    )
    if (
        permissions.network_access is not None
        and not permissions.extra_read_roots
        and not permissions.extra_write_roots
        and network_prefix
    ):
        session_label = _shell_network_session_option_label(network_prefix)
    elif (
        len(permissions.extra_read_roots) == 1
        and permissions.network_access is None
        and not permissions.extra_write_roots
    ):
        session_label = _filesystem_session_option_label(
            access_kind="read",
            root=permissions.extra_read_roots[0],
        )
    elif (
        len(permissions.extra_write_roots) == 1
        and permissions.network_access is None
        and not permissions.extra_read_roots
    ):
        session_label = _filesystem_session_option_label(
            access_kind="write",
            root=permissions.extra_write_roots[0],
        )

    return build_permission_approval_options(
        permissions=permissions,
        once_label="Allow once",
        session_label=session_label,
        network_command_prefix=network_prefix,
    )


def plan_shell_execution(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path,
    permission_memory,
) -> SandboxExecutionPlan:
    actions = extract_shell_permission_actions(
        permission_state=permission_state,
        command=command,
        shell_family=shell_family,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
    )
    evaluations = evaluate_permission_actions(actions=actions)
    approval_network_access: AdditionalNetworkAccess | None = None
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
        request_kind="command_execution",
        effective_permissions=None,
        approval_permissions=approval_permissions,
    )


def plan_file_access(
    *,
    permission_state: PermissionState,
    tool_path: str | None,
    action: str,
    access_kind: FileAccessKind,
    workspace_root: Path,
    permission_memory,
) -> FileAccessPlan:
    request_kind: ApprovalRequestKind = (
        "permission_grant" if access_kind == "read" else "file_change"
    )
    outside_workspace = False
    approval_scope_root: str | None = None
    actions: tuple[PermissionAction, ...] = ()
    if tool_path is not None:
        action_source: FileToolActionSource = (
            "read_tool" if access_kind == "read" else f"{action}_tool"
        )
        actions = extract_file_permission_actions(
            permission_state=permission_state,
            tool_path=tool_path,
            action_source=action_source,
            access_kind=access_kind,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
        )
        if actions:
            outside_workspace = actions[0].path_scope == "non_workspace"
            approval_scope_root = actions[0].root

    evaluations = evaluate_permission_actions(actions=actions)

    effective_permissions: AdditionalSandboxPermissions | None = None
    if (
        permission_state.effective_capabilities.filesystem_access != "full_access"
        and approval_scope_root is not None
        and outside_workspace
    ):
        if access_kind == "read":
            effective_permissions = AdditionalSandboxPermissions(
                extra_read_roots=(approval_scope_root,),
            )
        else:
            effective_permissions = AdditionalSandboxPermissions(
                extra_write_roots=(approval_scope_root,),
            )

    prompted_roots = tuple(
        evaluation.action.root
        for evaluation in evaluations
        if evaluation.match.decision == "prompt" and evaluation.action.root is not None
    )
    approval_permissions: AdditionalSandboxPermissions | None = None
    if prompted_roots:
        if access_kind == "read":
            approval_permissions = AdditionalSandboxPermissions(
                extra_read_roots=prompted_roots,
            )
        else:
            approval_permissions = AdditionalSandboxPermissions(
                extra_write_roots=prompted_roots,
            )

    return FileAccessPlan(
        sandbox_plan=derive_sandbox_execution_plan(
            permission_state=permission_state,
            request_kind=request_kind,
            effective_permissions=effective_permissions,
            approval_permissions=approval_permissions,
        ),
        effective_permissions=effective_permissions,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        request_kind=request_kind,
        approval_scope_root=approval_scope_root,
        approval_policy_label=describe_approval_policy_for_request_kind(
            approval_policy=permission_state.approval_policy,
            request_kind=request_kind,
        ),
    )


def _build_file_access_approval_request(
    file_access_plan: FileAccessPlan,
) -> FileChangeApprovalRequest | PermissionGrantApprovalRequest:
    sandbox_plan = file_access_plan.sandbox_plan
    options: tuple[ApprovalOption, ...] = ()
    if (
        sandbox_plan.requested_permissions is not None
        and file_access_plan.approval_scope_root is not None
    ):
        options = build_permission_approval_options(
            permissions=sandbox_plan.requested_permissions,
            once_label="Allow once",
            session_label=_filesystem_session_option_label(
                access_kind=file_access_plan.access_kind,
                root=file_access_plan.approval_scope_root,
            ),
        )

    reason = _file_access_approval_reason(file_access_plan)
    display_subject = _file_action_subject(
        action=file_access_plan.action,
        tool_path=file_access_plan.tool_path,
    )
    if file_access_plan.access_kind == "read":
        return PermissionGrantApprovalRequest(
            request_id=f"{file_access_plan.action}-{uuid4().hex}",
            request_kind="permission_grant",
            reason=reason,
            grant_kind="filesystem_read",
            target=file_access_plan.approval_scope_root,
            requested_capabilities=sandbox_plan.requested_capabilities,
            requested_permissions=sandbox_plan.requested_permissions,
            display_subject=display_subject,
            requested_grants=build_permission_grants(
                permissions=sandbox_plan.requested_permissions,
                filesystem_scope="session",
            ),
            options=options,
        )

    return FileChangeApprovalRequest(
        request_id=f"{file_access_plan.action}-{uuid4().hex}",
        request_kind="file_change",
        reason=reason,
        path=file_access_plan.tool_path or "",
        change_kind=(
            file_access_plan.action
            if file_access_plan.action in {"write", "edit"}
            else "write"
        ),
        requested_capabilities=sandbox_plan.requested_capabilities,
        requested_permissions=sandbox_plan.requested_permissions,
        display_subject=display_subject,
        requested_grants=build_permission_grants(
            permissions=sandbox_plan.requested_permissions,
            filesystem_scope="session",
        ),
        options=options,
    )


def _build_file_access_approval_requirement(
    file_access_plan: FileAccessPlan,
) -> ExecApprovalRequirement:
    if file_access_plan.sandbox_plan.approval_disposition == "allowed":
        return SkipApproval()

    request = _build_file_access_approval_request(file_access_plan)
    if file_access_plan.sandbox_plan.approval_disposition == "denied_by_policy":
        return ForbiddenApproval(
            request=request,
            denied_message=_policy_denied_message(request=request),
        )

    return NeedsApproval(
        request=request,
        denied_message=_approval_denied_message(request=request),
        missing_requester_message=(
            f"{file_access_plan.action.capitalize()} requires approval, "
            "but no approval requester is configured"
        ),
    )


async def approved_read_only_filesystem_policy(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
) -> FileSystemSandboxPolicy:
    file_access_plan = await _approved_file_access_plan(
        ctx=ctx,
        tool_path=tool_path,
        action=action,
        access_kind="read",
    )
    return derive_normalized_sandbox_policy(
        permission_state=ctx.deps.permission_state,
        additional_permissions=file_access_plan.effective_permissions,
    ).filesystem


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
) -> FileAccessPlan:
    file_access_plan = plan_file_access(
        permission_state=ctx.deps.permission_state,
        tool_path=tool_path,
        action=action,
        access_kind=access_kind,
        workspace_root=ctx.deps.workspace_root,
        permission_memory=ctx.deps.permission_memory,
    )
    await fulfill_approval_requirement(
        ctx=ctx,
        requirement=_build_file_access_approval_requirement(file_access_plan),
    )
    return file_access_plan


__all__ = [
    "approved_read_only_filesystem_policy",
    "build_shell_approval_options",
    "FileAccessPlan",
    "SandboxExecutionPlan",
    "describe_permission_delta",
    "describe_shell_permission_delta",
    "derive_sandbox_execution_plan",
    "extract_file_permission_actions",
    "extract_shell_permission_actions",
    "maybe_request_file_write_approval",
    "plan_file_access",
    "plan_shell_execution",
    "build_permission_grants",
]
