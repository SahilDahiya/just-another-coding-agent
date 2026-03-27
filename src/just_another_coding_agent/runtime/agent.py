from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.settings import ModelSettings

from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.registry import build_canonical_toolset

CANONICAL_AGENT_INSTRUCTIONS = "\n".join(
    [
        (
            "You are a headless coding assistant operating inside one "
            "configured workspace."
        ),
        "Use only these tools: read, write, edit, bash, grep, ls, find.",
        "Prefer read to examine files instead of bash cat or sed.",
        (
            "Use edit for precise surgical changes; it tries exact matching "
            "first and then a normalized fallback for minor formatting differences."
        ),
        "Use write only for new files or complete rewrites.",
        "Use grep for content search across files.",
        "Use ls for bounded directory listings.",
        "Use find for file discovery by glob pattern.",
        "Use bash for builds and commands.",
        (
            "Use read with offset and limit for large files instead of "
            "pulling everything at once."
        ),
        (
            "If a tool returns an object with ok: false, treat it as an "
            "operational error and decide the next corrective step yourself."
        ),
        (
            "Do not claim you created, edited, or saved a file unless you "
            "actually used write or edit, or verified the result with read or bash."
        ),
        (
            "After code changes or required file outputs, run the smallest "
            "relevant verification step before concluding."
        ),
        "Do not invent tools or alternate behaviors.",
        "Do not rely on fallbacks.",
        "Only uncaught tool failures end the run automatically.",
        "Keep responses concise and task-focused.",
        "Refer to files clearly by path.",
        "For read, write, and edit, relative paths resolve from the workspace root.",
        "bash runs in the workspace root and no tool is a filesystem sandbox.",
    ]
)


def build_canonical_instructions(
    *,
    workspace_root: Path | str,
    current_date: date | None = None,
) -> str:
    root = normalize_workspace_root(workspace_root)
    resolved_date = current_date or date.today()

    sections = [
        CANONICAL_AGENT_INSTRUCTIONS,
        f"Current date: {resolved_date.isoformat()}",
        f"Current workspace root: {root}",
    ]

    return "\n".join(sections)


def build_canonical_model_settings(
    *,
    thinking: ThinkingSetting | None = None,
) -> ModelSettings | None:
    if thinking is None:
        return None

    return {"thinking": thinking}


def build_canonical_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    history_processors: Sequence[Callable[[list[ModelMessage]], list[ModelMessage]]]
    | None = None,
) -> Agent[WorkspaceDeps, str]:
    root = normalize_workspace_root(workspace_root)

    return Agent(
        resolve_canonical_model(model),
        output_type=str,
        instructions=build_canonical_instructions(workspace_root=root),
        deps_type=WorkspaceDeps,
        toolsets=[
            build_canonical_toolset(tool_names)
        ],
        history_processors=history_processors,
    )


__all__ = [
    "CANONICAL_AGENT_INSTRUCTIONS",
    "build_canonical_agent",
    "build_canonical_instructions",
    "build_canonical_model_settings",
]
