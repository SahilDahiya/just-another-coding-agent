from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
)

from just_another_coding_agent.contracts.session import (
    SessionCompactionSummary,
    SessionRunRecord,
)

COMPACTION_SUMMARY_DYNAMIC_REF = "session-compaction-summary"


def build_compaction_summary_message(
    summary: SessionCompactionSummary,
) -> ModelRequest:
    lines = ["Session compaction summary:"]

    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")

    _append_summary_section(lines, "Established facts", summary.established_facts)
    _append_summary_section(lines, "User preferences", summary.user_preferences)
    _append_summary_section(lines, "Important paths", summary.important_paths)
    _append_summary_section(lines, "Read paths", summary.read_paths)
    _append_summary_section(lines, "Modified paths", summary.modified_paths)
    _append_summary_section(
        lines,
        "Recent shell commands",
        summary.recent_shell_commands,
    )
    _append_summary_section(lines, "Recent failures", summary.recent_failures)
    _append_summary_section(lines, "Open questions", summary.open_questions)
    _append_summary_section(lines, "Unresolved work", summary.unresolved_work)

    return ModelRequest(
        parts=[
            SystemPromptPart(
                content="\n".join(lines),
                dynamic_ref=COMPACTION_SUMMARY_DYNAMIC_REF,
            )
        ]
    )


def build_compaction_checkpoint_messages(
    *,
    summary: SessionCompactionSummary,
    retained_runs: Sequence[SessionRunRecord],
) -> list[ModelMessage]:
    return [
        build_compaction_summary_message(summary),
        *[
            message
            for run in retained_runs
            for message in run.messages
        ],
    ]
def _append_summary_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)
