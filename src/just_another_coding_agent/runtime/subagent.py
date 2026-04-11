from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic_ai.messages import ModelMessage

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
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps

SubagentRole = Literal["general", "explore", "verification"]
SubagentCapability = Literal["default", "shell"]
EPHEMERAL_SUBAGENT_TOOL_NAMES = ("read", "grep", "find", "ls")
SHELL_CAPABLE_SUBAGENT_TOOL_NAMES = (*EPHEMERAL_SUBAGENT_TOOL_NAMES, "shell")
_ROLE_LINES: dict[SubagentRole, tuple[str, ...]] = {
    "general": (
        (
            "Focus on the assigned bounded task and return concise findings "
            "to the parent agent."
        ),
    ),
    "explore": (
        (
            "Focus on locating the relevant files, flows, and evidence "
            "before drawing conclusions."
        ),
        "Cite concrete paths when you report findings.",
    ),
    "verification": (
        (
            "Focus on checking claims against the repository state and "
            "report mismatches explicitly."
        ),
    ),
}


@dataclass(frozen=True)
class EphemeralSubagentSpec:
    name: SessionName
    role: SubagentRole
    capability: SubagentCapability
    task: str
    parent_session_id: str
    parent_run_id: str


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


def build_ephemeral_subagent_instructions(
    *,
    role: SubagentRole,
    capability: SubagentCapability,
) -> str:
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
                    *_ROLE_LINES[role],
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
    capability: SubagentCapability,
) -> Any:
    from just_another_coding_agent.runtime.agent import build_canonical_agent

    return build_canonical_agent(
        model=model,
        workspace_root=workspace_root,
        tool_names=build_ephemeral_subagent_tool_names(capability),
        instructions=build_ephemeral_subagent_instructions(
            role=role,
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
        ),
    )


def _build_ephemeral_subagent_message_history(
    *,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None,
    shell_family: ShellFamily | None,
    timezone: str | None,
    thinking: ThinkingSetting | None,
) -> tuple[ModelMessage, ...]:
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


async def stream_run_events(**kwargs) -> AsyncIterator[RunEvent]:
    from just_another_coding_agent.runtime.run import stream_run_events as _stream

    async for event in _stream(**kwargs):
        yield event


async def stream_ephemeral_subagent_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    spec: EphemeralSubagentSpec,
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
        capability=spec.capability,
    )
    message_history = _build_ephemeral_subagent_message_history(
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
    "build_ephemeral_subagent_agent",
    "build_ephemeral_subagent_instructions",
    "build_ephemeral_subagent_tool_names",
    "build_ephemeral_subagent_workspace_deps",
    "stream_ephemeral_subagent_run_events",
]
