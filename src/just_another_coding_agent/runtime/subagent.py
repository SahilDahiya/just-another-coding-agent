from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic_ai.messages import ModelMessage, ToolCallPart, ToolReturnPart

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import RunEvent
from just_another_coding_agent.contracts.session import SessionName
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.prompt_layers import (
    PromptSection,
    build_base_product_prompt,
    build_prompt_context_layers,
)
from just_another_coding_agent.session.replacement_history import (
    strip_internal_prompt_state,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps

SubagentRole = Literal["general", "explore", "verification"]
SubagentSpawnMode = Literal["fresh", "fork"]
SubagentCapability = Literal["default", "shell"]
EPHEMERAL_SUBAGENT_TOOL_NAMES = ("read", "grep", "find", "ls")
SHELL_CAPABLE_SUBAGENT_TOOL_NAMES = (*EPHEMERAL_SUBAGENT_TOOL_NAMES, "shell")


@dataclass(frozen=True)
class SubagentRoleSpec:
    role: SubagentRole
    display_label: str
    running_summary: str
    prompt_lines: tuple[str, ...]


_SUBAGENT_ROLE_SPECS: dict[SubagentRole, SubagentRoleSpec] = {
    "general": SubagentRoleSpec(
        role="general",
        display_label="Subagent",
        running_summary="running child task",
        prompt_lines=(
            (
                "Focus on the assigned bounded task and return concise "
                "findings to the parent agent."
            ),
        ),
    ),
    "explore": SubagentRoleSpec(
        role="explore",
        display_label="Explore",
        running_summary="exploring repository",
        prompt_lines=(
            (
                "Focus on locating the relevant files, flows, and evidence "
                "before drawing conclusions."
            ),
            "Cite concrete paths when you report findings.",
        ),
    ),
    "verification": SubagentRoleSpec(
        role="verification",
        display_label="Verify",
        running_summary="verifying repository state",
        prompt_lines=(
            (
                "Focus on checking claims against the repository state and "
                "report mismatches explicitly."
            ),
        ),
    ),
}


def get_subagent_role_spec(role: SubagentRole) -> SubagentRoleSpec:
    return _SUBAGENT_ROLE_SPECS[role]


@dataclass(frozen=True)
class EphemeralSubagentSpec:
    name: SessionName
    role: SubagentRole
    spawn_mode: SubagentSpawnMode
    capability: SubagentCapability
    task: str
    parent_session_id: str
    parent_run_id: str
    parent_tool_call_id: str | None = None


def _build_subagent_output_contract_lines() -> tuple[str, ...]:
    return (
        "Follow any output-shape instructions in the assigned task exactly.",
        (
            "If the task does not specify an output shape, return concise "
            "plain text findings."
        ),
        "State concrete observations before conclusions when possible.",
        "Do not add markdown fences unless the task asks for them.",
    )


def build_ephemeral_subagent_tool_names(
    capability: SubagentCapability,
) -> tuple[str, ...]:
    if capability == "shell":
        return SHELL_CAPABLE_SUBAGENT_TOOL_NAMES
    return EPHEMERAL_SUBAGENT_TOOL_NAMES


def _capability_lines(
    capability: SubagentCapability,
) -> tuple[str, ...]:
    if capability == "shell":
        return (
            (
                "When the task needs local commands, scripts, or parsing "
                "beyond read/grep/find/ls, use shell directly and keep the "
                "work bounded."
            ),
            "You do not have write or edit tools in this run.",
        )
    return (
        "You do not have write, edit, or shell in this run.",
    )


def _spawn_mode_lines(
    spawn_mode: SubagentSpawnMode,
) -> tuple[str, ...]:
    if spawn_mode == "fork":
        return (
            (
                "You inherit a sanitized snapshot of the parent's current "
                "conversation history."
            ),
            (
                "Use inherited context as prior background, but stay focused "
                "on the assigned bounded task."
            ),
        )
    return (
        "You start without inheriting the parent conversation history.",
    )


def build_ephemeral_subagent_instructions(
    *,
    role: SubagentRole,
    spawn_mode: SubagentSpawnMode,
    capability: SubagentCapability,
) -> str:
    role_spec = get_subagent_role_spec(role)
    return build_base_product_prompt(
        tool_names=build_ephemeral_subagent_tool_names(capability),
        extra_sections=(
            PromptSection(
                name="subagent_scope",
                lines=(
                    (
                        "You are an ephemeral child agent handling one "
                        "bounded task."
                    ),
                    (
                        "You do not persist as a user session and you must "
                        "not claim file changes."
                    ),
                    *role_spec.prompt_lines,
                    *_spawn_mode_lines(spawn_mode),
                    *_capability_lines(capability),
                    *_build_subagent_output_contract_lines(),
                ),
            ),
        ),
    )


def build_ephemeral_subagent_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    role: SubagentRole,
    spawn_mode: SubagentSpawnMode,
    capability: SubagentCapability,
) -> Any:
    from just_another_coding_agent.runtime.agent import build_canonical_agent

    return build_canonical_agent(
        model=model,
        workspace_root=workspace_root,
        tool_names=build_ephemeral_subagent_tool_names(capability),
        instructions=build_ephemeral_subagent_instructions(
            role=role,
            spawn_mode=spawn_mode,
            capability=capability,
        ),
    )


def build_ephemeral_subagent_workspace_deps(
    *,
    parent_deps: WorkspaceDeps,
    spec: EphemeralSubagentSpec,
) -> WorkspaceDeps:
    return WorkspaceDeps(
        workspace_root=parent_deps.workspace_root,
        shell_family=parent_deps.shell_family,
        session_scope=RunSessionScope(
            kind="subagent",
            name=spec.name,
            parent_session_id=spec.parent_session_id,
            parent_run_id=spec.parent_run_id,
            parent_tool_call_id=spec.parent_tool_call_id,
        ),
    )


def _build_ephemeral_subagent_message_history(
    *,
    spawn_mode: SubagentSpawnMode,
    parent_message_history: Sequence[ModelMessage] | None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None,
    shell_family: ShellFamily | None,
    timezone: str | None,
    thinking: ThinkingSetting | None,
) -> tuple[ModelMessage, ...]:
    if spawn_mode == "fork":
        if parent_message_history is None:
            raise RuntimeError(
                "Forked subagent runs require parent message history"
            )
        return tuple(
            strip_internal_prompt_state(
                _strip_unresolved_tool_calls_from_messages(parent_message_history)
            )
        )

    layers = build_prompt_context_layers(
        baseline_decision=None,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=timezone,
        thinking=thinking,
    )
    return (
        *layers.before_history_messages,
        *layers.after_history_messages,
    )


def _strip_unresolved_tool_calls_from_messages(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    pending_tool_call_ids: set[str] = set()

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                pending_tool_call_ids.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                pending_tool_call_ids.discard(part.tool_call_id)

    if not pending_tool_call_ids:
        return list(messages)

    sanitized: list[ModelMessage] = []
    for message in messages:
        kept_parts = [
            part
            for part in message.parts
            if not (
                hasattr(part, "tool_call_id")
                and part.tool_call_id in pending_tool_call_ids
            )
        ]
        if not kept_parts:
            continue
        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue
        sanitized.append(replace(message, parts=kept_parts))

    return sanitized


async def stream_run_events(**kwargs) -> AsyncIterator[RunEvent]:
    from just_another_coding_agent.runtime.run import stream_run_events as _stream

    async for event in _stream(**kwargs):
        yield event


async def stream_ephemeral_subagent_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    spec: EphemeralSubagentSpec,
    parent_message_history: Sequence[ModelMessage] | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
    message_history_sink: Callable[[Sequence[ModelMessage]], None] | None = None,
) -> AsyncIterator[RunEvent]:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    resolved_shell_family = shell_family or detect_default_shell_family()
    deps = build_ephemeral_subagent_workspace_deps(
        parent_deps=WorkspaceDeps(
            workspace_root=normalized_workspace_root,
            shell_family=resolved_shell_family,
        ),
        spec=spec,
    )
    agent = build_ephemeral_subagent_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        role=spec.role,
        spawn_mode=spec.spawn_mode,
        capability=spec.capability,
    )
    message_history = _build_ephemeral_subagent_message_history(
        spawn_mode=spec.spawn_mode,
        parent_message_history=parent_message_history,
        model=model,
        workspace_root=normalized_workspace_root,
        current_date=current_date,
        shell_family=resolved_shell_family,
        timezone=timezone,
        thinking=thinking,
    )
    async for event in stream_run_events(
        agent=agent,
        prompt=spec.task,
        message_history=message_history,
        instructions=None,
        thinking=thinking,
        deps=deps,
        message_history_sink=message_history_sink,
        available_tool_names=build_ephemeral_subagent_tool_names(spec.capability),
    ):
        yield event


__all__ = [
    "EPHEMERAL_SUBAGENT_TOOL_NAMES",
    "EphemeralSubagentSpec",
    "SubagentCapability",
    "SubagentRole",
    "SubagentRoleSpec",
    "SubagentSpawnMode",
    "build_ephemeral_subagent_agent",
    "build_ephemeral_subagent_instructions",
    "build_ephemeral_subagent_tool_names",
    "build_ephemeral_subagent_workspace_deps",
    "get_subagent_role_spec",
    "stream_ephemeral_subagent_run_events",
]
