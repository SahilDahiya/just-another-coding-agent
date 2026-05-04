"""Workspace trust resolution and persistence."""

from __future__ import annotations

import tempfile
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


def _global_temp_root() -> Path:
    return Path(tempfile.gettempdir()).expanduser().resolve()


def resolve_workspace_trust_target(workspace_root: Path | str) -> Path:
    current = _canonical_workspace_root(workspace_root)
    temp_root = _global_temp_root()
    for candidate in (current, *current.parents):
        # Ignore a global temp directory marker such as `/tmp/.git`. That is
        # not a meaningful repo trust boundary for an arbitrary nested
        # workspace created under the temp root.
        if candidate != current and candidate == temp_root:
            break
        if (candidate / ".git").exists():
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


def _trust_key(trust_target: Path) -> str:
    return f"{_TRUST_KEY_PREFIX}{trust_target}"


__all__ = [
    "WorkspaceTrustStatus",
    "accept_workspace_trust",
    "resolve_workspace_trust_target",
    "workspace_trust_status",
]
