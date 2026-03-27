from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import FunctionToolset
from pydantic_ai.toolsets import AbstractToolset

from just_another_coding_agent.contracts.tools import (
    CANONICAL_TOOL_NAMES,
    CanonicalToolName,
)
from just_another_coding_agent.tools.bash import BASH_TOOL
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import EDIT_TOOL
from just_another_coding_agent.tools.errors import ErrorWrappingToolset
from just_another_coding_agent.tools.find import FIND_TOOL
from just_another_coding_agent.tools.grep import GREP_TOOL
from just_another_coding_agent.tools.ls import LS_TOOL
from just_another_coding_agent.tools.read import READ_TOOL
from just_another_coding_agent.tools.write import WRITE_TOOL


class UnknownToolError(KeyError):
    """Raised when a requested tool name is outside the canonical registry."""


_TOOLS_BY_NAME = {
    "bash": BASH_TOOL,
    "edit": EDIT_TOOL,
    "find": FIND_TOOL,
    "grep": GREP_TOOL,
    "ls": LS_TOOL,
    "read": READ_TOOL,
    "write": WRITE_TOOL,
}


def list_canonical_tool_names() -> tuple[CanonicalToolName, ...]:
    return CANONICAL_TOOL_NAMES


def build_canonical_toolset(
    tool_names: Sequence[str],
) -> AbstractToolset[WorkspaceDeps]:
    resolved_tools = []
    seen_names: set[str] = set()

    for tool_name in tool_names:
        if tool_name in seen_names:
            raise ValueError(f"Duplicate tool name: {tool_name}")
        seen_names.add(tool_name)

        if tool_name not in CANONICAL_TOOL_NAMES:
            raise UnknownToolError(f"Unknown canonical tool: {tool_name}")

        resolved_tools.append(_TOOLS_BY_NAME[tool_name])

    return ErrorWrappingToolset(
        FunctionToolset[WorkspaceDeps](resolved_tools, strict=True)
    )


__all__ = [
    "UnknownToolError",
    "build_canonical_toolset",
    "list_canonical_tool_names",
]
