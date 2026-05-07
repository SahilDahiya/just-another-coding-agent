from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import FunctionToolset
from pydantic_ai.toolsets import AbstractToolset

from just_another_coding_agent.contracts.tools import (
    CANONICAL_TOOL_NAMES,
    CanonicalToolName,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import EDIT_TOOL
from just_another_coding_agent.tools.errors import ErrorWrappingToolset
from just_another_coding_agent.tools.find import FIND_TOOL
from just_another_coding_agent.tools.grep import GREP_TOOL
from just_another_coding_agent.tools.ls import LS_TOOL
from just_another_coding_agent.tools.onboarding_question import (
    ASK_ONBOARDING_QUESTION_TOOL,
)
from just_another_coding_agent.tools.read import READ_TOOL
from just_another_coding_agent.tools.shell import SHELL_TOOL
from just_another_coding_agent.tools.subagent import SUBAGENT_TOOL
from just_another_coding_agent.tools.write import WRITE_TOOL


class UnknownToolError(KeyError):
    """Raised when a requested tool name is outside the canonical registry."""


PARALLEL_CANONICAL_TOOL_NAMES = (
    "read",
    "grep",
    "find",
    "ls",
)
SEQUENTIAL_CANONICAL_TOOL_NAMES = (
    "write",
    "edit",
    "shell",
    "subagent",
    "ask_onboarding_question",
)

_TOOLS_BY_NAME = {
    "ask_onboarding_question": ASK_ONBOARDING_QUESTION_TOOL,
    "edit": EDIT_TOOL,
    "find": FIND_TOOL,
    "grep": GREP_TOOL,
    "ls": LS_TOOL,
    "read": READ_TOOL,
    "shell": SHELL_TOOL,
    "subagent": SUBAGENT_TOOL,
    "write": WRITE_TOOL,
}

if set(PARALLEL_CANONICAL_TOOL_NAMES).isdisjoint(SEQUENTIAL_CANONICAL_TOOL_NAMES):
    if set(PARALLEL_CANONICAL_TOOL_NAMES) | set(SEQUENTIAL_CANONICAL_TOOL_NAMES) != set(
        CANONICAL_TOOL_NAMES
    ):
        raise RuntimeError("Canonical tool concurrency policy must cover all tools")
else:
    raise RuntimeError("Canonical tool concurrency policy must be disjoint")


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

        tool = _TOOLS_BY_NAME[tool_name]
        expected_sequential = tool_name in SEQUENTIAL_CANONICAL_TOOL_NAMES
        if tool.sequential is not expected_sequential:
            raise RuntimeError(
                f"Canonical tool {tool_name} has sequential={tool.sequential}, "
                f"expected {expected_sequential}"
            )
        resolved_tools.append(tool)

    return ErrorWrappingToolset(
        FunctionToolset[WorkspaceDeps](resolved_tools, strict=True)
    )


__all__ = [
    "PARALLEL_CANONICAL_TOOL_NAMES",
    "SEQUENTIAL_CANONICAL_TOOL_NAMES",
    "UnknownToolError",
    "build_canonical_toolset",
    "list_canonical_tool_names",
]
