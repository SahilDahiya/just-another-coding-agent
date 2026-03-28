from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from just_another_coding_agent.contracts.run_events import JsonValue
from just_another_coding_agent.tools._workspace import normalize_workspace_root

ToolUpdateSink: TypeAlias = Callable[
    [str, str, JsonValue | None],
    Awaitable[None],
]


@dataclass(frozen=True)
class WorkspaceDeps:
    workspace_root: Path
    tool_update_sink: ToolUpdateSink | None = None

    @classmethod
    def from_workspace_root(cls, workspace_root: Path | str) -> WorkspaceDeps:
        return cls(workspace_root=normalize_workspace_root(workspace_root))


__all__ = ["ToolUpdateSink", "WorkspaceDeps"]
