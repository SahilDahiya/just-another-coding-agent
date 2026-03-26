from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from pi_code_agent.contracts.tools import CANONICAL_TOOL_NAMES
from pi_code_agent.tools.registry import build_canonical_toolset


def build_canonical_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> Agent[Any, str]:
    return Agent(
        model,
        output_type=str,
        toolsets=[
            build_canonical_toolset(
                tool_names,
                workspace_root=workspace_root,
            )
        ],
    )


__all__ = ["build_canonical_agent"]
