from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from just_another_coding_agent.tools._workspace import normalize_workspace_root


@dataclass(frozen=True)
class WorkspaceDeps:
    workspace_root: Path

    @classmethod
    def from_workspace_root(cls, workspace_root: Path | str) -> WorkspaceDeps:
        return cls(workspace_root=normalize_workspace_root(workspace_root))


__all__ = ["WorkspaceDeps"]
