from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import FunctionToolset

from pi_code_agent.contracts.tools import CANONICAL_TOOL_NAMES, CanonicalToolName
from pi_code_agent.tools.read import READ_TOOL
from pi_code_agent.tools.write import WRITE_TOOL


class UnknownToolError(KeyError):
    """Raised when a requested tool name is outside the canonical registry."""


class ToolNotImplementedError(NotImplementedError):
    """Raised when a canonical tool name exists but has no implementation yet."""


_IMPLEMENTED_TOOLS = {
    "read": READ_TOOL,
    "write": WRITE_TOOL,
}


def list_canonical_tool_names() -> tuple[CanonicalToolName, ...]:
    return CANONICAL_TOOL_NAMES


def build_canonical_toolset(tool_names: Sequence[str]) -> FunctionToolset[None]:
    resolved_tools = []
    seen_names: set[str] = set()

    for tool_name in tool_names:
        if tool_name in seen_names:
            raise ValueError(f"Duplicate tool name: {tool_name}")
        seen_names.add(tool_name)

        if tool_name not in CANONICAL_TOOL_NAMES:
            raise UnknownToolError(f"Unknown canonical tool: {tool_name}")

        tool = _IMPLEMENTED_TOOLS.get(tool_name)
        if tool is None:
            raise ToolNotImplementedError(
                f"Canonical tool not implemented yet: {tool_name}"
            )

        resolved_tools.append(tool)

    return FunctionToolset(resolved_tools, strict=True)


__all__ = [
    "ToolNotImplementedError",
    "UnknownToolError",
    "build_canonical_toolset",
    "list_canonical_tool_names",
]
