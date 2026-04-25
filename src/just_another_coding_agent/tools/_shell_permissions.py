from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Literal

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import PermissionState
from just_another_coding_agent.tools._permission_actions import (
    filesystem_path_permission_action,
)
from just_another_coding_agent.tools._policy_engine import PermissionAction

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


def extract_shell_permission_actions(
    *,
    permission_state: PermissionState,
    command: str,
    shell_family: ShellFamily,
    workspace_root: Path,
    permission_memory,
) -> tuple[PermissionAction, ...]:
    tokens = _shell_command_tokens(
        command=command,
        shell_family=shell_family,
    )
    if tokens is None:
        return ()

    actions: list[PermissionAction] = []
    network_command_prefix = _tokens_network_command_prefix(tokens)
    if _tokens_request_network_access(tokens):
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
        _tokens_read_actions(
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            tokens=tokens,
        )
    )
    actions.extend(
        _tokens_write_actions(
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            tokens=tokens,
        )
    )
    return tuple(actions)


def shell_network_command_prefix(
    command: str,
    *,
    shell_family: ShellFamily,
) -> tuple[str, ...]:
    tokens = _shell_command_tokens(
        command=command,
        shell_family=shell_family,
    )
    if tokens is None:
        return ()
    return _tokens_network_command_prefix(tokens)


def _shell_command_tokens(
    *,
    command: str,
    shell_family: ShellFamily,
) -> tuple[str, ...] | None:
    if shell_family != "posix":
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    return _unwrap_shell_wrappers(tokens)


def _unwrap_shell_wrappers(
    tokens: list[str],
    *,
    _depth: int = 0,
) -> tuple[str, ...] | None:
    if not tokens or _depth > 4:
        return None
    executable = tokens[0]
    if executable not in _SHELL_WRAPPERS:
        return tuple(tokens)
    unwrapped = _unwrap_shell_wrapper(tokens)
    if unwrapped is None:
        return tuple(tokens)
    return _unwrap_shell_wrappers(unwrapped, _depth=_depth + 1)


def _tokens_request_network_access(tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return False

    executable = tokens[0]
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


def _tokens_network_command_prefix(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if not tokens:
        return ()
    executable = tokens[0]
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
    return _shell_dash_c_tokens(tokens)


def _shell_dash_c_tokens(tokens: list[str]) -> list[str] | None:
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


def _python_command_requests_network(tokens: tuple[str, ...]) -> bool:
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


def _tokens_write_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    tokens: tuple[str, ...],
) -> tuple[PermissionAction, ...]:
    if not tokens:
        return ()

    executable = tokens[0]
    actions: list[PermissionAction] = []
    seen_targets: set[tuple[str | None, str | None]] = set()
    write_command = executable in _SHELL_FILESYSTEM_WRITE_COMMANDS
    destination_write_index = _last_positional_path_index(tokens)
    if executable not in _SHELL_SOURCE_AND_DESTINATION_WRITE_COMMANDS:
        destination_write_index = None

    for index, token in enumerate(tokens[1:], start=1):
        path_token, write_requested = _write_candidate_from_token(
            executable=executable,
            tokens=tokens,
            index=index,
            token=token,
            write_command=write_command,
            destination_write_index=destination_write_index,
        )
        if path_token is None or not write_requested:
            continue

        _append_unique_filesystem_action(
            actions=actions,
            seen_targets=seen_targets,
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            path_token=path_token,
            action_kind="filesystem_write",
            extracted_by="shell_write_heuristics",
            workspace_write_covered_by_current_permissions=(
                permission_state.effective_capabilities.filesystem_access
                in {"workspace_write", "full_access"}
            ),
        )

    return tuple(actions)


def _write_candidate_from_token(
    *,
    executable: str,
    tokens: tuple[str, ...],
    index: int,
    token: str,
    write_command: bool,
    destination_write_index: int | None,
) -> tuple[str | None, bool]:
    previous = tokens[index - 1]
    if executable == "dd":
        dd_path = _dd_path_candidate_from_shell_token(token)
        if dd_path is None:
            return None, False
        dd_key, path_token = dd_path
        return path_token, dd_key == "of"

    path_token = _path_candidate_from_shell_token(token)
    if path_token is None:
        return None, False
    if previous in _WRITE_REDIRECTION_TOKENS:
        return path_token, True
    if previous in _READ_REDIRECTION_TOKENS:
        return path_token, False
    if destination_write_index is not None:
        return path_token, index == destination_write_index
    return path_token, write_command


def _tokens_read_actions(
    *,
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    tokens: tuple[str, ...],
) -> tuple[PermissionAction, ...]:
    if not tokens or tokens[0] not in _SHELL_FILESYSTEM_READ_COMMANDS:
        return ()

    actions: list[PermissionAction] = []
    seen_targets: set[tuple[str | None, str | None]] = set()
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

        _append_unique_filesystem_action(
            actions=actions,
            seen_targets=seen_targets,
            permission_state=permission_state,
            workspace_root=workspace_root,
            permission_memory=permission_memory,
            path_token=path_token,
            action_kind="filesystem_read",
            extracted_by="shell_read_heuristics",
        )

    return tuple(actions)


def _append_unique_filesystem_action(
    *,
    actions: list[PermissionAction],
    seen_targets: set[tuple[str | None, str | None]],
    permission_state: PermissionState,
    workspace_root: Path,
    permission_memory,
    path_token: str,
    action_kind: Literal["filesystem_read", "filesystem_write"],
    extracted_by: str,
    workspace_write_covered_by_current_permissions: bool = True,
) -> None:
    action = filesystem_path_permission_action(
        permission_state=permission_state,
        workspace_root=workspace_root,
        permission_memory=permission_memory,
        tool_path=path_token,
        action_kind=action_kind,
        source="shell",
        extracted_by=extracted_by,
        workspace_write_covered_by_current_permissions=(
            workspace_write_covered_by_current_permissions
        ),
    )
    key = (action.path_scope, action.root)
    if key in seen_targets:
        return
    seen_targets.add(key)
    actions.append(action)


def _last_positional_path_index(tokens: tuple[str, ...]) -> int | None:
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


__all__ = [
    "extract_shell_permission_actions",
    "shell_network_command_prefix",
]
