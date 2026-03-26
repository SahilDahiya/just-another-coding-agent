from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from pi_code_agent.contracts.tools import CANONICAL_TOOL_NAMES
from pi_code_agent.tools.registry import build_canonical_toolset

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
        "Check bash exit_code in tool results; non-zero means the command failed.",
        "Do not invent tools or alternate behaviors.",
        "Do not rely on fallbacks.",
        "If a tool call fails, treat it as a real failure.",
        "Keep responses concise and task-focused.",
        "Refer to files clearly by path.",
        "read, write, and edit are scoped to the configured workspace root.",
        "bash runs in the workspace root but is not a filesystem sandbox.",
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
