from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    SystemPromptPart,
)

from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionSummary,
)

COMPACTION_SUMMARY_DYNAMIC_REF = "session-compaction-summary"


def build_resume_message_history(loaded_session: LoadedSession) -> list[ModelMessage]:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return loaded_session.message_history

    if latest_compaction.first_kept_run_id is not None:
        retained_start_index = _run_index_for_id(
            loaded_session,
            latest_compaction.first_kept_run_id,
        )
    else:
        retained_start_index = (
            _run_index_for_id(
                loaded_session,
                latest_compaction.summarized_through_run_id,
            )
            + 1
        )

    retained_messages = [
        message
        for run in loaded_session.runs[retained_start_index:]
        for message in run.messages
    ]
    return [
        build_compaction_summary_message(latest_compaction.summary),
        *retained_messages,
    ]


def strip_compaction_summary_from_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    sanitized: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            sanitized.append(message)
            continue

        kept_parts = [
            part for part in message.parts if not _is_compaction_summary_part(part)
        ]
        if not kept_parts:
            continue

        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue

        sanitized.append(replace(message, parts=kept_parts))

    return sanitized


def build_compaction_summary_message(
    summary: SessionCompactionSummary,
) -> ModelRequest:
    lines = ["Session compaction summary:"]

    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")

    _append_summary_section(lines, "Established facts", summary.established_facts)
    _append_summary_section(lines, "User preferences", summary.user_preferences)
    _append_summary_section(lines, "Important paths", summary.important_paths)
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


def _append_summary_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)


def _is_compaction_summary_part(part: ModelRequestPart) -> bool:
    return (
        isinstance(part, SystemPromptPart)
        and part.dynamic_ref == COMPACTION_SUMMARY_DYNAMIC_REF
    )


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")
