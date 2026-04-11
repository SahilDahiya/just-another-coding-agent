from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.project_docs import (
    PROJECT_DOC_TOTAL_BYTE_BUDGET,
    build_project_doc_prefix_messages,
)

if TYPE_CHECKING:
    from just_another_coding_agent.contracts.platform import ShellFamily
    from just_another_coding_agent.contracts.thinking import ThinkingSetting
    from just_another_coding_agent.runtime.turn_context import (
        TurnContextBaselineDecision,
    )


@dataclass(frozen=True)
class PromptSection:
    name: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class PromptContextLayers:
    """Layers: base policy, project docs, runtime frame, mode overlay."""

    base_instructions: str
    project_messages: tuple[ModelMessage, ...]
    runtime_before_history_messages: tuple[ModelMessage, ...]
    runtime_after_history_messages: tuple[ModelMessage, ...]
    mode_messages: tuple[ModelMessage, ...] = ()

    @property
    def before_history_messages(self) -> tuple[ModelMessage, ...]:
        return (
            *self.project_messages,
            *self.runtime_before_history_messages,
            *self.mode_messages,
        )

    @property
    def after_history_messages(self) -> tuple[ModelMessage, ...]:
        return self.runtime_after_history_messages


_IDENTITY_SECTION = PromptSection(
    name="identity",
    lines=(
        (
            "You are a headless coding assistant operating inside one "
            "configured workspace."
        ),
    ),
)
_TOOL_FAILURE_POLICY_SECTION = PromptSection(
    name="tool_failure_policy",
    lines=(
        (
            "If a tool returns an object with ok: false, treat it as an "
            "operational error and decide the next corrective step yourself."
        ),
    ),
)
_FAILURE_SEMANTICS_SECTION = PromptSection(
    name="failure_semantics",
    lines=(
        "Do not invent tools or alternate behaviors.",
        "Do not rely on fallbacks.",
        "Only uncaught tool failures end the run automatically.",
    ),
)
_RESPONSE_STYLE_SECTION = PromptSection(
    name="response_style",
    lines=(
        "Default response style: brief, direct, and outcome-first.",
        (
            "Do not restate the user's request or narrate routine process "
            "unless that context is necessary."
        ),
        (
            "During work, keep progress updates to one short sentence focused "
            "on the next action or concrete finding."
        ),
        (
            "Final answers should usually be one short paragraph: state what "
            "changed or what you found, then mention verification or blockers."
        ),
        (
            "Use bullets only when there are multiple distinct findings, "
            "steps, or options."
        ),
        (
            "If no files changed, answer the question directly without a "
            "change-style summary."
        ),
    ),
)
_FILESYSTEM_TRUTH_SECTION = PromptSection(
    name="filesystem_truth",
    lines=(
        "Refer to files clearly by path.",
        (
            "For read, write, and edit, relative paths resolve from the "
            "workspace root."
        ),
        "shell runs in the workspace root and no tool is a filesystem sandbox.",
    ),
)
_TOOL_GUIDANCE_BY_NAME = {
    "read": (
        "Prefer read to examine files instead of shelling out just to view files.",
        (
            "Use read with offset and limit for large files instead of "
            "pulling everything at once."
        ),
    ),
    "write": ("Use write only for new files or complete rewrites.",),
    "edit": (
        (
            "Use edit for precise surgical changes; it tries exact matching "
            "first and then a normalized fallback for minor formatting "
            "differences."
        ),
    ),
    "shell": ("Use shell for builds, commands, and verification.",),
    "grep": ("Use grep for content search across files.",),
    "ls": ("Use ls for bounded directory listings.",),
    "find": ("Use find for file discovery by glob pattern.",),
    "subagent": (
        (
            "Use subagent for one bounded side task when either a fresh or "
            "forked child pass would help."
        ),
        (
            "Good fits: locating relevant files or evidence, checking one "
            "claim against repository state, or inspecting one large "
            "artifact for the parent."
        ),
        (
            "Prefer spawn_mode='fork' so the child can build on the "
            "parent's current conversation or tool context; use "
            "spawn_mode='fresh' only for an independent repo or artifact "
            "pass."
        ),
        (
            "By default the child gets read, grep, find, and ls only; "
            "request shell capability only when the child needs local "
            "commands or scripts."
        ),
        (
            "Do not use subagent for broad multi-step work or when the next "
            "local command is already obvious."
        ),
        (
            "When you spawn a child, make the task detailed enough to "
            "succeed: state the exact goal, relevant files or artifacts, "
            "constraints, stop condition, and desired report shape when "
            "needed."
        ),
    ),
}


def _dedupe_tool_names(tool_names: Sequence[str]) -> tuple[str, ...]:
    ordered = tuple(tool_names)
    if len(ordered) != len(set(ordered)):
        raise ValueError("Tool prompt policy requires unique tool names")
    return ordered


def build_tool_policy_lines(
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> tuple[str, ...]:
    resolved_tool_names = _dedupe_tool_names(tool_names)
    if not resolved_tool_names:
        return ("No tools are available in this run.",)
    lines = [f"Use only these tools: {', '.join(resolved_tool_names)}."]
    for tool_name in resolved_tool_names:
        try:
            lines.extend(_TOOL_GUIDANCE_BY_NAME[tool_name])
        except KeyError as error:
            raise ValueError(
                f"Unknown tool name in prompt policy: {tool_name}"
            ) from error
    return tuple(lines)


def build_verification_policy_lines(
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> tuple[str, ...]:
    resolved_tool_names = set(_dedupe_tool_names(tool_names))
    can_mutate = "write" in resolved_tool_names or "edit" in resolved_tool_names
    can_shell = "shell" in resolved_tool_names
    if not can_mutate and not can_shell:
        return (
            (
                "This run is inspection-only. Do not claim you created, "
                "edited, or saved files."
            ),
        )
    if not can_mutate:
        return (
            "You do not have write or edit tools in this run.",
            (
                "Do not claim file changes unless you actually changed files "
                "through shell and verified the result with read or shell."
            ),
        )
    verification_tools = "read or shell" if can_shell else "read"
    return (
        (
            "Do not claim you created, edited, or saved a file unless you "
            "actually used write or edit, or verified the result with "
            f"{verification_tools}."
        ),
        (
            "After code changes or required file outputs, run the smallest "
            "relevant verification step before concluding."
        ),
        (
            "When the user asks to run tests, lint, or another obvious "
            "verification step, run the narrowest relevant command directly; "
            "inspect first only if the command or scope is ambiguous."
        ),
    )


def _build_sections_with_layout(
    *,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    extra_sections: Sequence[PromptSection] = (),
) -> tuple[PromptSection, ...]:
    return (
        _IDENTITY_SECTION,
        PromptSection(
            name="tool_policy",
            lines=build_tool_policy_lines(tool_names),
        ),
        _TOOL_FAILURE_POLICY_SECTION,
        PromptSection(
            name="verification_policy",
            lines=build_verification_policy_lines(tool_names),
        ),
        *tuple(extra_sections),
        _FAILURE_SEMANTICS_SECTION,
        _RESPONSE_STYLE_SECTION,
        _FILESYSTEM_TRUTH_SECTION,
    )


BASE_PRODUCT_PROMPT_SECTIONS: tuple[PromptSection, ...] = _build_sections_with_layout()


def build_base_product_prompt(
    *,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    extra_sections: Sequence[PromptSection] = (),
) -> str:
    sections = (
        BASE_PRODUCT_PROMPT_SECTIONS
        if tuple(tool_names) == CANONICAL_TOOL_NAMES and not extra_sections
        else _build_sections_with_layout(
            tool_names=tool_names,
            extra_sections=extra_sections,
        )
    )
    return "\n".join(
        line
        for section in sections
        for line in section.lines
    )


def build_default_mode_messages() -> tuple[ModelMessage, ...]:
    return ()


def build_prompt_context_layers(
    *,
    baseline_decision: "TurnContextBaselineDecision | None" = None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: "ShellFamily | None" = None,
    timezone: str | None = None,
    thinking: "ThinkingSetting | None" = None,
    project_doc_total_byte_budget: int = PROJECT_DOC_TOTAL_BYTE_BUDGET,
) -> PromptContextLayers:
    from just_another_coding_agent.runtime.turn_context import (
        build_runtime_context_injection_plan,
    )

    _, project_messages = build_project_doc_prefix_messages(
        workspace_root,
        total_byte_budget=project_doc_total_byte_budget,
    )
    runtime_plan = build_runtime_context_injection_plan(
        baseline_decision=baseline_decision,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=timezone,
        thinking=thinking,
    )
    return PromptContextLayers(
        base_instructions=build_base_product_prompt(),
        project_messages=project_messages,
        runtime_before_history_messages=runtime_plan.before_history_messages,
        runtime_after_history_messages=runtime_plan.after_history_messages,
        mode_messages=build_default_mode_messages(),
    )


__all__ = [
    "BASE_PRODUCT_PROMPT_SECTIONS",
    "PromptContextLayers",
    "PromptSection",
    "build_base_product_prompt",
    "build_default_mode_messages",
    "build_prompt_context_layers",
]
