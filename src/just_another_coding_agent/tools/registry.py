from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic_ai import FunctionToolset

from just_another_coding_agent.contracts.tools import (
    CANONICAL_TOOL_NAMES,
    CanonicalToolName,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.bash import create_bash_tool
from just_another_coding_agent.tools.edit import create_edit_tool
from just_another_coding_agent.tools.find import create_find_tool
from just_another_coding_agent.tools.grep import create_grep_tool
from just_another_coding_agent.tools.ls import create_ls_tool
from just_another_coding_agent.tools.read import create_read_tool
from just_another_coding_agent.tools.write import create_write_tool


class UnknownToolError(KeyError):
    """Raised when a requested tool name is outside the canonical registry."""


class ToolNotImplementedError(NotImplementedError):
    """Raised when a canonical tool name exists but has no implementation yet."""


_TOOL_FACTORIES = {
    "bash": create_bash_tool,
    "edit": create_edit_tool,
    "find": create_find_tool,
    "grep": create_grep_tool,
    "ls": create_ls_tool,
    "read": create_read_tool,
    "write": create_write_tool,
}


def list_canonical_tool_names() -> tuple[CanonicalToolName, ...]:
    return CANONICAL_TOOL_NAMES


def build_canonical_toolset(
    tool_names: Sequence[str],
    *,
    workspace_root: Path | str,
) -> FunctionToolset[None]:
    root = normalize_workspace_root(workspace_root)
    resolved_tools = []
    seen_names: set[str] = set()

    for tool_name in tool_names:
        if tool_name in seen_names:
            raise ValueError(f"Duplicate tool name: {tool_name}")
        seen_names.add(tool_name)

        if tool_name not in CANONICAL_TOOL_NAMES:
            raise UnknownToolError(f"Unknown canonical tool: {tool_name}")

        tool_factory = _TOOL_FACTORIES.get(tool_name)
        if tool_factory is None:
            raise ToolNotImplementedError(
                f"Canonical tool not implemented yet: {tool_name}"
            )

        resolved_tools.append(tool_factory(workspace_root=root))

    return FunctionToolset(resolved_tools, strict=True)


__all__ = [
    "ToolNotImplementedError",
    "UnknownToolError",
    "build_canonical_toolset",
    "list_canonical_tool_names",
]
