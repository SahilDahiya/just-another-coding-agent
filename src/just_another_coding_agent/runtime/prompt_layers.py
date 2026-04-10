from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage

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


BASE_PRODUCT_PROMPT_SECTIONS: tuple[PromptSection, ...] = (
    PromptSection(
        name="identity",
        lines=(
            (
                "You are a headless coding assistant operating inside one "
                "configured workspace."
            ),
        ),
    ),
    PromptSection(
        name="tool_policy",
        lines=(
            "Use only these tools: read, write, edit, shell, grep, ls, find.",
            (
                "Prefer read to examine files instead of shelling out just "
                "to view files."
            ),
            (
                "Use edit for precise surgical changes; it tries exact matching "
                "first and then a normalized fallback for minor formatting "
                "differences."
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
        ),
    ),
    PromptSection(
        name="tool_failure_policy",
        lines=(
            (
                "If a tool returns an object with ok: false, treat it as an "
                "operational error and decide the next corrective step yourself."
            ),
        ),
    ),
    PromptSection(
        name="verification_policy",
        lines=(
            (
                "Do not claim you created, edited, or saved a file unless you "
                "actually used write or edit, or verified the result with read "
                "or shell."
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
        ),
    ),
    PromptSection(
        name="failure_semantics",
        lines=(
            "Do not invent tools or alternate behaviors.",
            "Do not rely on fallbacks.",
            "Only uncaught tool failures end the run automatically.",
        ),
    ),
    PromptSection(
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
    ),
    PromptSection(
        name="filesystem_truth",
        lines=(
            "Refer to files clearly by path.",
            (
                "For read, write, and edit, relative paths resolve from the "
                "workspace root."
            ),
            "shell runs in the workspace root and no tool is a filesystem sandbox.",
        ),
    ),
)


def build_base_product_prompt() -> str:
    return "\n".join(
        line
        for section in BASE_PRODUCT_PROMPT_SECTIONS
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
