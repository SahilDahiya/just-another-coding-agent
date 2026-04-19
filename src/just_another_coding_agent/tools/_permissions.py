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
    ApprovalRequest,
    EffectiveCapabilities,
    FileSystemSandboxPolicy,
    NormalizedSandboxPolicy,
    PermissionState,
    derive_normalized_sandbox_policy,
    derive_requested_capabilities,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools._workspace import (
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
}
_GIT_NETWORK_SUBCOMMANDS = frozenset(
    {
        "clone",
        "fetch",
        "ls-remote",
        "pull",
        "push",
    }
)
_SHELL_WRAPPERS = frozenset({"bash", "sh", "dash", "zsh", "env", "sudo", "timeout"})
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
_SHELL_SOURCE_AND_DESTINATION_WRITE_COMMANDS = frozenset(
    {
        "cp",
        "install",
        "ln",
        "mv",
    }
)
_WRITE_REDIRECTION_TOKENS = frozenset(
    {
        ">",
        ">>",
        "1>",
        "1>>",
        "2>",
        "2>>",
    }
)
_READ_REDIRECTION_TOKENS = frozenset(
    {
        "<",
        "0<",
        "<<",
        "<<<",
    }
)
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


@dataclass(frozen=True)
class SandboxExecutionPlan:
    requested_permissions: AdditionalSandboxPermissions | None
    requested_capabilities: EffectiveCapabilities
    normalized_policy: NormalizedSandboxPolicy
    approval_required: bool


FileAccessKind = Literal["read", "write"]


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
    if executable == "uv":
        return _uv_command_requests_network(tokens[1:])
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
    return any(_token_looks_like_network_target(token) for token in tokens)


def _uv_command_requests_network(tokens: list[str]) -> bool:
    if not tokens:
        return False
    first = tokens[0]
    if first == "pip" and len(tokens) > 1:
        return tokens[1] in _NETWORK_PACKAGE_MANAGER_SUBCOMMANDS["pip"]
    if first == "tool" and len(tokens) > 1:
        return tokens[1] in {"install", "upgrade"}
    if first in {"sync", "lock", "add", "remove", "publish", "runx", "x"}:
        return True
    return any(_token_looks_like_network_target(token) for token in tokens)


def _token_looks_like_network_target(token: str) -> bool:
    lower_token = token.lower()
    return (
        "://" in lower_token
        or lower_token.startswith("git@")
        or lower_token.startswith("ssh://")
        or "github.com/" in lower_token
        or "gitlab.com/" in lower_token
    )


def _shell_command_requested_filesystem_permissions(
    *,
    workspace_root: Path,
    permission_memory,
    command: str,
    shell_family: ShellFamily,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if shell_family != "posix":
        return (), (), (), ()
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return (), (), (), ()
    if not tokens:
        return (), (), (), ()
    return _tokens_requested_filesystem_permissions(
        workspace_root=workspace_root,
        permission_memory=permission_memory,
        tokens=tokens,
    )


def _tokens_requested_filesystem_permissions(
    *,
    workspace_root: Path,
    permission_memory,
    tokens: list[str],
    _depth: int = 0,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not tokens or _depth > 4:
        return (), (), (), ()
    executable = tokens[0]
    if executable in _SHELL_WRAPPERS:
        unwrapped = _unwrap_shell_wrapper(tokens)
        if unwrapped is not None:
            return _tokens_requested_filesystem_permissions(
                workspace_root=workspace_root,
                permission_memory=permission_memory,
                tokens=unwrapped,
                _depth=_depth + 1,
            )

    effective_read_roots: list[str] = []
    approval_read_roots: list[str] = []
    effective_write_roots: list[str] = []
    approval_write_roots: list[str] = []
    seen_effective_reads: set[str] = set()
    seen_effective_writes: set[str] = set()
    seen_approval_reads: set[str] = set()
    seen_approval_writes: set[str] = set()
    write_command = executable in _SHELL_FILESYSTEM_WRITE_COMMANDS
    destination_write_index = _last_positional_path_index(tokens)
    if executable not in _SHELL_SOURCE_AND_DESTINATION_WRITE_COMMANDS:
        destination_write_index = None

    for index, token in enumerate(tokens[1:], start=1):
        previous = tokens[index - 1]
        path_token = _path_candidate_from_shell_token(token)
        if path_token is None:
            continue
        resolved = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path_token,
        )
        if path_is_within_workspace(
            workspace_root=workspace_root,
            resolved_path=resolved,
        ):
            continue
        scope_root = _approval_scope_root(resolved)
        if previous in _WRITE_REDIRECTION_TOKENS:
            read_requested = False
            write_requested = True
        elif previous in _READ_REDIRECTION_TOKENS:
            read_requested = True
            write_requested = False
        elif destination_write_index is not None:
            read_requested = index != destination_write_index
            write_requested = index == destination_write_index
        else:
            read_requested = previous not in _WRITE_REDIRECTION_TOKENS
            write_requested = (
                write_command and previous not in _READ_REDIRECTION_TOKENS
            )
        if read_requested and scope_root not in seen_effective_reads:
            seen_effective_reads.add(scope_root)
            effective_read_roots.append(scope_root)
            if not permission_memory.allows_read_path(Path(scope_root)):
                seen_approval_reads.add(scope_root)
                approval_read_roots.append(scope_root)
        if write_requested and scope_root not in seen_effective_writes:
            seen_effective_writes.add(scope_root)
            effective_write_roots.append(scope_root)
            if not permission_memory.allows_write_path(Path(scope_root)):
                seen_approval_writes.add(scope_root)
                approval_write_roots.append(scope_root)

    return (
        tuple(effective_read_roots),
        tuple(approval_read_roots),
        tuple(effective_write_roots),
        tuple(approval_write_roots),
    )


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
    return SandboxExecutionPlan(
        requested_permissions=approval_permissions,
        requested_capabilities=derive_requested_capabilities(
            permission_state=permission_state,
            additional_permissions=effective_permissions,
        ),
        normalized_policy=derive_normalized_sandbox_policy(
            permission_state=permission_state,
            additional_permissions=effective_permissions,
        ),
        approval_required=approval_required,
    )

def _approval_scope_root(resolved_path: Path) -> str:
    scope_root = (
        resolved_path
        if resolved_path.exists() and resolved_path.is_dir()
        else resolved_path.parent
    )
    return str(scope_root.resolve())


def plan_shell_execution(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path | None = None,
    permission_memory=None,
) -> SandboxExecutionPlan:
    effective_network_access = None
    approval_network_access = None
    if (
        permission_state.approval_policy.mode == "on_escalation"
        and permission_state.sandbox_policy.mode == "workspace_write"
        and _shell_command_requests_network_access(
            command=command,
            shell_family=shell_family,
        )
    ):
        effective_network_access = "enabled"
        approval_network_access = "enabled"
    effective_read_roots: tuple[str, ...] = ()
    approval_read_roots: tuple[str, ...] = ()
    effective_write_roots: tuple[str, ...] = ()
    approval_write_roots: tuple[str, ...] = ()
    if (
        workspace_root is not None
        and permission_memory is not None
        and permission_state.approval_policy.mode == "on_escalation"
        and permission_state.sandbox_policy.mode == "workspace_write"
    ):
        (
            effective_read_roots,
            approval_read_roots,
            effective_write_roots,
            approval_write_roots,
        ) = _shell_command_requested_filesystem_permissions(
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            command=command,
            shell_family=shell_family,
        )
    effective_permissions: AdditionalSandboxPermissions | None = None
    if (
        effective_network_access is not None
        or effective_read_roots
        or effective_write_roots
    ):
        effective_permissions = AdditionalSandboxPermissions(
            network_access=effective_network_access,
            extra_read_roots=effective_read_roots,
            extra_write_roots=effective_write_roots,
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
        effective_permissions=effective_permissions,
        approval_permissions=approval_permissions,
    )


async def approved_read_only_filesystem_policy(
    *,
    ctx: ToolExecutionContext,
    tool_path: str | None,
    action: str,
) -> FileSystemSandboxPolicy:
    if ctx.deps.permission_state.sandbox_policy.mode == "workspace_write":
        return FileSystemSandboxPolicy(access="full_access")
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
        if outside_workspace:
            approval_scope_root = _approval_scope_root(resolved)
            if access_kind == "read":
                if permission_state.sandbox_policy.mode != "workspace_write":
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
    decision = await ctx.deps.approval_requester(
        ApprovalRequest(
            request_id=f"{action}-{uuid4().hex}",
            reason=(
                f"{reason_prefix}: {truncate_activity_label(tool_path)}"
            ),
            requested_capabilities=plan.requested_capabilities,
            requested_permissions=plan.requested_permissions,
        )
    )
    if decision.decision != "approved":
        raise RuntimeError(
            f"{action.capitalize()} approval did not return an approved decision"
        )
    if approval_scope_root is not None and approval_permissions is not None:
        remember_approved_permissions(
            permission_memory=ctx.deps.permission_memory,
            permissions=approval_permissions,
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


__all__ = [
    "approved_read_only_filesystem_policy",
    "SandboxExecutionPlan",
    "derive_sandbox_execution_plan",
    "maybe_request_file_write_approval",
    "plan_shell_execution",
    "remember_approved_permissions",
]
