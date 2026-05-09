from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeAlias

from just_another_coding_agent.contracts.onboarding import (
    OnboardingAnswerResult,
    OnboardingQuestionRequest,
)
from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import JsonValue
from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalRequest,
    PermissionState,
    SandboxPermissionGrant,
    build_default_permission_state,
)
from just_another_coding_agent.contracts.session import SessionName
from just_another_coding_agent.contracts.teaching import (
    TeachingRelationship,
    TeachingSnippet,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.code_mode.python_runtime import (
    PythonSubprocessCodeModeRuntime,
)
from just_another_coding_agent.runtime.code_mode.service import (
    CodeModeCellService,
    CodeModeRunner,
)
from just_another_coding_agent.tools._workspace import (
    canonicalize_path_target,
    normalize_workspace_root,
)
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
    [ApprovalRequest, str | None, str | None],
    Awaitable[ApprovalDecision],
]
OnboardingQuestionRequester: TypeAlias = Callable[
    [OnboardingQuestionRequest],
    Awaitable[OnboardingAnswerResult],
]
RunSessionKind: TypeAlias = Literal["root", "subagent"]


def _canonicalize_permission_root(root: str) -> str:
    return str(canonicalize_path_target(root))


def _canonicalize_candidate_path(path: Path) -> Path:
    return canonicalize_path_target(path)


def _path_is_within_root(*, path: Path, root: str) -> bool:
    return _canonicalize_candidate_path(path).is_relative_to(Path(root))


@dataclass
class SessionPermissionMemory:
    approved_read_roots: set[str] = field(default_factory=set)
    approved_write_roots: set[str] = field(default_factory=set)
    approved_command_prefixes: set[tuple[str, ...]] = field(default_factory=set)

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
        self.approved_read_roots.add(_canonicalize_permission_root(root))

    def remember_write_root(self, root: str) -> None:
        self.approved_write_roots.add(_canonicalize_permission_root(root))

    def allows_command_prefix(self, tokens: tuple[str, ...]) -> bool:
        return any(
            len(prefix) <= len(tokens) and tokens[: len(prefix)] == prefix
            for prefix in self.approved_command_prefixes
        )

    def remember_command_prefix(self, prefix: tuple[str, ...]) -> None:
        if prefix:
            self.approved_command_prefixes.add(prefix)

    def snapshot_session_grants(self) -> tuple[SandboxPermissionGrant, ...]:
        grants: list[SandboxPermissionGrant] = []
        if self.approved_read_roots or self.approved_write_roots:
            grants.append(
                SandboxPermissionGrant(
                    permissions=AdditionalSandboxPermissions(
                        extra_read_roots=tuple(sorted(self.approved_read_roots)),
                        extra_write_roots=tuple(
                            sorted(self.approved_write_roots)
                        ),
                    ),
                    scope="session",
                )
            )
        for prefix in sorted(self.approved_command_prefixes):
            grants.append(
                SandboxPermissionGrant(
                    permissions=AdditionalSandboxPermissions(
                        network_access="enabled",
                    ),
                    scope="session",
                    command_prefix=prefix,
                )
            )
        return tuple(grants)

    def remember_session_grants(
        self,
        grants: tuple[SandboxPermissionGrant, ...],
    ) -> None:
        self.clear()
        for grant in grants:
            if grant.scope != "session":
                continue
            for root in grant.permissions.extra_read_roots:
                self.remember_read_root(root)
            for root in grant.permissions.extra_write_roots:
                self.remember_write_root(root)
            if grant.command_prefix:
                self.remember_command_prefix(grant.command_prefix)

    def clear(self) -> None:
        self.approved_read_roots.clear()
        self.approved_write_roots.clear()
        self.approved_command_prefixes.clear()


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
class TeachingPacketRecord:
    packet_id: str
    run_id: str
    title: str
    concept: str
    relationships: tuple[TeachingRelationship, ...]
    snippets: tuple[TeachingSnippet, ...]


@dataclass
class TeachingPacketRegistry:
    packets_by_id: dict[str, TeachingPacketRecord] = field(default_factory=dict)

    def remember(
        self,
        *,
        packet_id: str,
        run_id: str,
        title: str,
        concept: str,
        relationships: tuple[TeachingRelationship, ...],
        snippets: tuple[TeachingSnippet, ...],
    ) -> TeachingPacketRecord:
        record = TeachingPacketRecord(
            packet_id=packet_id,
            run_id=run_id,
            title=title,
            concept=concept,
            relationships=relationships,
            snippets=snippets,
        )
        self.packets_by_id[packet_id] = record
        return record

    def resolve_for_run(
        self,
        *,
        packet_ids: tuple[str, ...],
        run_id: str,
    ) -> tuple[TeachingPacketRecord, ...]:
        resolved: list[TeachingPacketRecord] = []
        for packet_id in packet_ids:
            record = self.packets_by_id.get(packet_id)
            if record is None or record.run_id != run_id:
                raise KeyError(packet_id)
            resolved.append(record)
        return tuple(resolved)


@dataclass(frozen=True)
class WorkspaceDeps:
    workspace_root: Path
    sessions_root: Path | None = None
    shell_family: ShellFamily = "posix"
    session_scope: RunSessionScope = field(default_factory=RunSessionScope)
    run_frame: RunRuntimeFrame | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    teaching_packet_registry: TeachingPacketRegistry = field(
        default_factory=TeachingPacketRegistry,
        compare=False,
        repr=False,
    )
    tool_update_sink: ToolUpdateSink | None = None
    approval_requester: ApprovalRequester | None = None
    onboarding_question_requester: OnboardingQuestionRequester | None = None
    code_mode_cell_service: CodeModeCellService = field(
        default_factory=CodeModeCellService,
        compare=False,
        repr=False,
    )
    code_mode_runner: CodeModeRunner | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    code_mode_source_runtime: PythonSubprocessCodeModeRuntime = field(
        default_factory=PythonSubprocessCodeModeRuntime,
        compare=False,
        repr=False,
    )
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
    def from_workspace_root(
        cls,
        workspace_root: Path | str,
        *,
        code_mode_runner: CodeModeRunner | None = None,
    ) -> WorkspaceDeps:
        return cls(
            workspace_root=normalize_workspace_root(workspace_root),
            shell_family=detect_default_shell_family(),
            code_mode_runner=code_mode_runner,
        )

    async def close_runtime_resources(self) -> None:
        await self.code_mode_source_runtime.close()
        await self.read_only_worker.close()


__all__ = [
    "RunRuntimeFrame",
    "RunSessionKind",
    "RunSessionScope",
    "ApprovalRequester",
    "OnboardingQuestionRequester",
    "SessionPermissionMemory",
    "TeachingPacketRecord",
    "TeachingPacketRegistry",
    "ToolUpdateSink",
    "WorkspaceDeps",
]
