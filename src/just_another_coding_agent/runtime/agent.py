from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.compaction import build_in_run_history_processor
from just_another_coding_agent.runtime.models import (
    build_in_run_compaction_soft_char_limit,
    resolve_canonical_model,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.registry import build_canonical_toolset

CANONICAL_AGENT_INSTRUCTIONS = "\n".join(
    [
        (
            "You are a headless coding assistant operating inside one "
            "configured workspace."
        ),
        "Use only these tools: read, write, edit, shell, grep, ls, find.",
        "Prefer read to examine files instead of shelling out just to view files.",
        (
            "Use edit for precise surgical changes; it tries exact matching "
            "first and then a normalized fallback for minor formatting differences."
        ),
        "Use write only for new files or complete rewrites.",
        "Use grep for content search across files.",
        "Use ls for bounded directory listings.",
        "Use find for file discovery by glob pattern.",
        "Use shell for builds, commands, and verification.",
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
            "actually used write or edit, or verified the result with read or shell."
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
        "shell runs in the workspace root and no tool is a filesystem sandbox.",
    ]
)


def _shell_family_prompt_label(shell_family: ShellFamily) -> str:
    if shell_family == "powershell":
        return "powershell"
    return "posix (bash)"


def build_canonical_instructions(
    *,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
) -> str:
    root = normalize_workspace_root(workspace_root)
    resolved_date = current_date or date.today()
    effective_shell_family = shell_family or detect_default_shell_family()

    sections = [
        CANONICAL_AGENT_INSTRUCTIONS,
        f"Current date: {resolved_date.isoformat()}",
        f"Current workspace root: {root}",
        f"Current shell family: {_shell_family_prompt_label(effective_shell_family)}",
    ]

    return "\n".join(sections)


def build_canonical_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    history_processors: Sequence[
        Callable[
            [list[ModelMessage]],
            list[ModelMessage] | Awaitable[list[ModelMessage]],
        ]
    ]
    | None = None,
) -> Agent[WorkspaceDeps, str]:
    root = normalize_workspace_root(workspace_root)
    effective_shell_family = shell_family or detect_default_shell_family()
    resolved_model = resolve_canonical_model(model)
    effective_history_processors = list(history_processors or [])
    effective_history_processors.append(
        build_in_run_history_processor(
            soft_char_limit=build_in_run_compaction_soft_char_limit(
                resolved_model
            )
        )
    )

    return Agent(
        resolved_model,
        output_type=str,
        instructions=build_canonical_instructions(
            workspace_root=root,
            shell_family=effective_shell_family,
        ),
        deps_type=WorkspaceDeps,
        toolsets=[
            build_canonical_toolset(tool_names)
        ],
        history_processors=effective_history_processors,
    )


__all__ = [
    "CANONICAL_AGENT_INSTRUCTIONS",
    "build_canonical_agent",
    "build_canonical_instructions",
]
