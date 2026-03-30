from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import JsonValue
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)

ToolUpdateSink: TypeAlias = Callable[
    [str, str, JsonValue | None],
    Awaitable[None],
]


@dataclass(frozen=True)
class WorkspaceDeps:
    workspace_root: Path
    shell_family: ShellFamily = "posix"
    tool_update_sink: ToolUpdateSink | None = None
    read_only_worker: ReadOnlyWorkerRuntime = field(
        default_factory=ReadOnlyWorkerRuntime,
        compare=False,
        repr=False,
    )

    @classmethod
    def from_workspace_root(cls, workspace_root: Path | str) -> WorkspaceDeps:
        return cls(
            workspace_root=normalize_workspace_root(workspace_root),
            shell_family=detect_default_shell_family(),
        )


__all__ = ["ToolUpdateSink", "WorkspaceDeps"]
