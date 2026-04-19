"""Workspace trust resolution and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from just_another_coding_agent.config import load_config, save_config

_TRUST_KEY_PREFIX = "project_trust:"
_TRUSTED_VALUE = "trusted"


@dataclass(frozen=True)
class WorkspaceTrustStatus:
    trust_target: str
    trusted: bool


def _canonical_workspace_root(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve()


def _looks_like_git_root(candidate: Path) -> bool:
    marker = candidate / ".git"
    if marker.is_dir():
        return (marker / "HEAD").is_file()
    if not marker.is_file():
        return False
    try:
        first_line = marker.read_text(encoding="utf-8").splitlines()[0].strip()
    except (IndexError, OSError, UnicodeDecodeError):
        return False
    if not first_line.startswith("gitdir:"):
        return False
    raw_git_dir = first_line.removeprefix("gitdir:").strip()
    if not raw_git_dir:
        return False
    git_dir = Path(raw_git_dir)
    if not git_dir.is_absolute():
        git_dir = (candidate / git_dir).resolve()
    return git_dir.is_dir() and (git_dir / "HEAD").is_file()


def resolve_workspace_trust_target(workspace_root: Path | str) -> Path:
    current = _canonical_workspace_root(workspace_root)
    for candidate in (current, *current.parents):
        if _looks_like_git_root(candidate):
            return candidate
    return current


def workspace_trust_status(workspace_root: Path | str) -> WorkspaceTrustStatus:
    trust_target = resolve_workspace_trust_target(workspace_root)
    config = load_config()
    trusted = config.get(_trust_key(trust_target), "") == _TRUSTED_VALUE
    return WorkspaceTrustStatus(
        trust_target=str(trust_target),
        trusted=trusted,
    )


def accept_workspace_trust(workspace_root: Path | str) -> WorkspaceTrustStatus:
    trust_target = resolve_workspace_trust_target(workspace_root)
    config = load_config()
    config[_trust_key(trust_target)] = _TRUSTED_VALUE
    save_config(config)
    return WorkspaceTrustStatus(
        trust_target=str(trust_target),
        trusted=True,
    )


def revoke_workspace_trust(workspace_root: Path | str) -> WorkspaceTrustStatus:
    trust_target = resolve_workspace_trust_target(workspace_root)
    config = load_config()
    config.pop(_trust_key(trust_target), None)
    save_config(config)
    return WorkspaceTrustStatus(
        trust_target=str(trust_target),
        trusted=False,
    )


def _trust_key(trust_target: Path) -> str:
    return f"{_TRUST_KEY_PREFIX}{trust_target}"


__all__ = [
    "WorkspaceTrustStatus",
    "accept_workspace_trust",
    "revoke_workspace_trust",
    "resolve_workspace_trust_target",
    "workspace_trust_status",
]
