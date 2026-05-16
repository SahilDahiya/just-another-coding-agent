from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import FunctionToolset
from pydantic_ai.toolsets import AbstractToolset

from just_another_coding_agent.contracts.code_mode import CODE_MODE_TOOL_NAMES
from just_another_coding_agent.contracts.mcp import JACA_ONBOARDING_MCP_TOOL_NAMES
from just_another_coding_agent.contracts.run_mode import (
    DEFAULT_RUN_MODE,
    ONBOARDING_RUN_MODE,
    RunMode,
)
from just_another_coding_agent.contracts.tools import (
    CANONICAL_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    MCP_DISCOVERY_TOOL_NAMES,
    ONBOARDING_TOOL_NAMES,
    CanonicalToolName,
    OnboardingToolName,
)
from just_another_coding_agent.tools.code_mode import (
    CODE_MODE_EXEC_TOOL,
    CODE_MODE_WAIT_TOOL,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import EDIT_TOOL
from just_another_coding_agent.tools.errors import ErrorWrappingToolset
from just_another_coding_agent.tools.find import FIND_TOOL
from just_another_coding_agent.tools.grep import GREP_TOOL
from just_another_coding_agent.tools.ls import LS_TOOL
from just_another_coding_agent.tools.mcp_search import MCP_SEARCH_TOOL
from just_another_coding_agent.tools.mcq_from_teaching_packets import (
    GENERATE_MCQ_FROM_TEACHING_PACKETS_TOOL,
)
from just_another_coding_agent.tools.onboarding_question import (
    ASK_MCQ_QUESTION_TOOL,
)
from just_another_coding_agent.tools.read import READ_TOOL
from just_another_coding_agent.tools.shell import SHELL_TOOL
from just_another_coding_agent.tools.subagent import SUBAGENT_TOOL
from just_another_coding_agent.tools.teaching_packet import (
    PUBLISH_TEACHING_PACKET_TOOL,
)
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
)
SEQUENTIAL_ONBOARDING_TOOL_NAMES = (
    "ask_mcq_question",
    "generate_mcq_from_teaching_packets",
    "publish_teaching_packet",
)
SEQUENTIAL_CODE_MODE_TOOL_NAMES = CODE_MODE_TOOL_NAMES
SEQUENTIAL_MCP_DISCOVERY_TOOL_NAMES = MCP_DISCOVERY_TOOL_NAMES

_TOOLS_BY_NAME = {
    "ask_mcq_question": ASK_MCQ_QUESTION_TOOL,
    "edit": EDIT_TOOL,
    "exec": CODE_MODE_EXEC_TOOL,
    "find": FIND_TOOL,
    "generate_mcq_from_teaching_packets": (GENERATE_MCQ_FROM_TEACHING_PACKETS_TOOL),
    "grep": GREP_TOOL,
    "ls": LS_TOOL,
    "mcp_search": MCP_SEARCH_TOOL,
    "publish_teaching_packet": PUBLISH_TEACHING_PACKET_TOOL,
    "read": READ_TOOL,
    "shell": SHELL_TOOL,
    "subagent": SUBAGENT_TOOL,
    "wait": CODE_MODE_WAIT_TOOL,
    "write": WRITE_TOOL,
}

if set(PARALLEL_CANONICAL_TOOL_NAMES).isdisjoint(SEQUENTIAL_CANONICAL_TOOL_NAMES):
    if set(PARALLEL_CANONICAL_TOOL_NAMES) | set(SEQUENTIAL_CANONICAL_TOOL_NAMES) != set(
        CANONICAL_TOOL_NAMES
    ):
        raise RuntimeError("Canonical tool concurrency policy must cover all tools")
else:
    raise RuntimeError("Canonical tool concurrency policy must be disjoint")

if set(PARALLEL_CANONICAL_TOOL_NAMES).isdisjoint(SEQUENTIAL_ONBOARDING_TOOL_NAMES):
    if set(PARALLEL_CANONICAL_TOOL_NAMES) | set(SEQUENTIAL_CANONICAL_TOOL_NAMES) | set(
        SEQUENTIAL_ONBOARDING_TOOL_NAMES
    ) | set(SEQUENTIAL_MCP_DISCOVERY_TOOL_NAMES) != set(KNOWN_TOOL_NAMES):
        raise RuntimeError("Known tool concurrency policy must cover all tools")
else:
    raise RuntimeError("Onboarding tool concurrency policy must be disjoint")

if not set(SEQUENTIAL_ONBOARDING_TOOL_NAMES).isdisjoint(
    SEQUENTIAL_MCP_DISCOVERY_TOOL_NAMES
):
    raise RuntimeError("MCP discovery tool concurrency policy must be disjoint")

if not set(SEQUENTIAL_CODE_MODE_TOOL_NAMES).issubset(_TOOLS_BY_NAME):
    raise RuntimeError("Code Mode tool policy references unknown tools")


def list_canonical_tool_names() -> tuple[CanonicalToolName, ...]:
    return CANONICAL_TOOL_NAMES


def list_onboarding_tool_names() -> tuple[OnboardingToolName, ...]:
    return ONBOARDING_TOOL_NAMES


def resolve_tool_names_for_run_mode(mode: RunMode) -> tuple[str, ...]:
    if mode == DEFAULT_RUN_MODE:
        return KNOWN_TOOL_NAMES[: len(CANONICAL_TOOL_NAMES)]
    if mode == ONBOARDING_RUN_MODE:
        return (*CANONICAL_TOOL_NAMES, *JACA_ONBOARDING_MCP_TOOL_NAMES)
    raise ValueError(f"Unknown run mode: {mode}")


def build_canonical_toolset(
    tool_names: Sequence[str],
) -> AbstractToolset[WorkspaceDeps]:
    resolved_tools = []
    seen_names: set[str] = set()

    for tool_name in tool_names:
        if tool_name in seen_names:
            raise ValueError(f"Duplicate tool name: {tool_name}")
        seen_names.add(tool_name)

        if tool_name not in _TOOLS_BY_NAME:
            raise UnknownToolError(f"Unknown canonical tool: {tool_name}")

        tool = _TOOLS_BY_NAME[tool_name]
        expected_sequential = (
            tool_name in SEQUENTIAL_CANONICAL_TOOL_NAMES
            or tool_name in SEQUENTIAL_ONBOARDING_TOOL_NAMES
            or tool_name in SEQUENTIAL_CODE_MODE_TOOL_NAMES
            or tool_name in SEQUENTIAL_MCP_DISCOVERY_TOOL_NAMES
        )
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
    "CODE_MODE_TOOL_NAMES",
    "PARALLEL_CANONICAL_TOOL_NAMES",
    "SEQUENTIAL_CODE_MODE_TOOL_NAMES",
    "SEQUENTIAL_CANONICAL_TOOL_NAMES",
    "SEQUENTIAL_ONBOARDING_TOOL_NAMES",
    "UnknownToolError",
    "build_canonical_toolset",
    "list_canonical_tool_names",
    "list_onboarding_tool_names",
    "resolve_tool_names_for_run_mode",
]
