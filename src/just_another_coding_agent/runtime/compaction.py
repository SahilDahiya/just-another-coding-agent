from __future__ import annotations

from collections.abc import Callable

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
AUTO_COMPACTION_RUN_THRESHOLD = 5


def build_session_history_processor(
    loaded_session: LoadedSession,
) -> Callable[[list[ModelMessage]], list[ModelMessage]] | None:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return None

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    retained_messages = [
        message
        for run in loaded_session.runs[summary_run_index + 1 :]
        for message in run.messages
    ]
    compacted_prefix = [
        build_compaction_summary_message(latest_compaction.summary),
        *retained_messages,
    ]

    def apply_compaction(messages: list[ModelMessage]) -> list[ModelMessage]:
        if messages and _starts_with_compaction_summary(messages):
            return messages

        persisted_prefix_length = _leading_persisted_message_count(messages)
        if persisted_prefix_length == 0:
            raise RuntimeError(
                "Compaction history processor could not match the expected "
                "persisted history prefix"
            )

        return compacted_prefix + messages[persisted_prefix_length:]

    return apply_compaction


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

        sanitized.append(message.model_copy(update={"parts": kept_parts}))

    return sanitized


def should_auto_compact_session(loaded_session: LoadedSession) -> bool:
    if not loaded_session.runs:
        return False

    return (
        _runs_since_latest_compaction(loaded_session)
        >= AUTO_COMPACTION_RUN_THRESHOLD
    )


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


def _starts_with_compaction_summary(messages: list[ModelMessage]) -> bool:
    if not messages:
        return False

    first_message = messages[0]
    if not isinstance(first_message, ModelRequest):
        return False

    return any(_is_compaction_summary_part(part) for part in first_message.parts)


def _leading_persisted_message_count(messages: list[ModelMessage]) -> int:
    count = 0
    for message in messages:
        if message.run_id is not None:
            break
        count += 1
    return count


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")


def _runs_since_latest_compaction(loaded_session: LoadedSession) -> int:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return len(loaded_session.runs)

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return len(loaded_session.runs[summary_run_index + 1 :])


__all__ = [
    "AUTO_COMPACTION_RUN_THRESHOLD",
    "COMPACTION_SUMMARY_DYNAMIC_REF",
    "build_compaction_summary_message",
    "build_session_history_processor",
    "should_auto_compact_session",
    "strip_compaction_summary_from_messages",
]
