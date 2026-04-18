from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeAlias

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import JsonValue
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalRequest,
    PermissionState,
    build_default_permission_state,
)
from just_another_coding_agent.contracts.session import SessionName
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.sandbox_executor import (
    HostSandboxExecutor,
    SandboxExecutor,
)

ToolUpdateSink: TypeAlias = Callable[
    [str, str, JsonValue | None],
    Awaitable[None],
]
ApprovalRequester: TypeAlias = Callable[
    [ApprovalRequest],
    Awaitable[ApprovalDecision],
]
RunSessionKind: TypeAlias = Literal["root", "subagent"]


def _path_is_within_root(*, path: Path, root: str) -> bool:
    return path.is_relative_to(Path(root))


@dataclass
class SessionPermissionMemory:
    approved_read_roots: set[str] = field(default_factory=set)
    approved_write_roots: set[str] = field(default_factory=set)

    def allows_read_path(self, path: Path) -> bool:
        return any(
            _path_is_within_root(path=path, root=root)
            for root in self.approved_read_roots
        )

    def allows_write_path(self, path: Path) -> bool:
        return any(
            _path_is_within_root(path=path, root=root)
            for root in self.approved_write_roots
        )

    def remember_read_root(self, root: str) -> None:
        self.approved_read_roots.add(root)

    def remember_write_root(self, root: str) -> None:
        self.approved_write_roots.add(root)

    def clear(self) -> None:
        self.approved_read_roots.clear()
        self.approved_write_roots.clear()


@dataclass(frozen=True)
class RunSessionScope:
    kind: RunSessionKind = "root"
    name: SessionName | None = None
    session_id: str | None = None
    run_id: str | None = None
    parent_session_id: str | None = None
    parent_run_id: str | None = None
    parent_tool_call_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "run_id",
            "parent_session_id",
            "parent_run_id",
            "parent_tool_call_id",
        ):
            value = getattr(self, field_name)
            if value == "":
                raise ValueError(f"Run session scope {field_name} cannot be empty")
        if self.kind == "root":
            if (
                self.parent_session_id is not None
                or self.parent_run_id is not None
                or self.parent_tool_call_id is not None
            ):
                raise ValueError(
                    "Root session scope cannot declare parent session lineage"
                )
            return
        if self.name is None:
            raise ValueError("Subagent session scope requires a session name")
        if self.parent_session_id is None or self.parent_run_id is None:
            raise ValueError(
                "Subagent session scope requires parent session and run ids"
            )


@dataclass(frozen=True)
class RunRuntimeFrame:
    model: Any = field(default=None, compare=False, repr=False)
    current_date: date | None = None
    timezone: str | None = None
    thinking: ThinkingSetting | None = None


@dataclass(frozen=True)
class WorkspaceDeps:
    workspace_root: Path
    shell_family: ShellFamily = "posix"
    session_scope: RunSessionScope = field(default_factory=RunSessionScope)
    run_frame: RunRuntimeFrame | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    tool_update_sink: ToolUpdateSink | None = None
    approval_requester: ApprovalRequester | None = None
    read_only_worker: ReadOnlyWorkerRuntime = field(
        default_factory=ReadOnlyWorkerRuntime,
        compare=False,
        repr=False,
    )
    permission_state: PermissionState = field(
        default_factory=build_default_permission_state,
        compare=False,
        repr=False,
    )
    permission_memory: SessionPermissionMemory = field(
        default_factory=SessionPermissionMemory,
        compare=False,
        repr=False,
    )
    sandbox_executor: SandboxExecutor = field(
        default_factory=HostSandboxExecutor,
        compare=False,
        repr=False,
    )

    @classmethod
    def from_workspace_root(cls, workspace_root: Path | str) -> WorkspaceDeps:
        return cls(
            workspace_root=normalize_workspace_root(workspace_root),
            shell_family=detect_default_shell_family(),
        )


__all__ = [
    "RunRuntimeFrame",
    "RunSessionKind",
    "RunSessionScope",
    "ApprovalRequester",
    "ToolUpdateSink",
    "SessionPermissionMemory",
    "WorkspaceDeps",
]
