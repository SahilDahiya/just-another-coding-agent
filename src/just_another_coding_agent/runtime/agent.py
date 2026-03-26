from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.tools.registry import build_canonical_toolset

CANONICAL_AGENT_INSTRUCTIONS = "\n".join(
    [
        (
            "You are a headless coding assistant operating inside one "
            "configured workspace."
        ),
        "Use only these tools: read, write, edit, bash.",
        "Prefer read before edit.",
        "Use edit for precise changes.",
        "Use write only for new files or full rewrites.",
        "Use bash for search, inspection, and commands.",
        (
            "Use read with offset and limit for large files instead of "
            "pulling everything at once."
        ),
        (
            "If a tool returns an object with ok: false, treat it as an "
            "operational error and decide the next corrective step yourself."
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


def build_canonical_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> Agent[Any, str]:
    return Agent(
        model,
        output_type=str,
        instructions=CANONICAL_AGENT_INSTRUCTIONS,
        toolsets=[
            build_canonical_toolset(
                tool_names,
                workspace_root=workspace_root,
            )
        ],
    )


__all__ = ["CANONICAL_AGENT_INSTRUCTIONS", "build_canonical_agent"]
