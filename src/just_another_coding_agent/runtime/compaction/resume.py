from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolReturnPart,
)

from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionSummary,
)

COMPACTION_SUMMARY_DYNAMIC_REF = "session-compaction-summary"

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


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
    persisted_history = loaded_session.message_history
    persisted_history_prefix_dump = _MODEL_MESSAGES_ADAPTER.dump_python(
        persisted_history,
        mode="json",
    )
    cleaned_persisted_history = _clean_history_for_prefix_match(
        persisted_history
    )
    cleaned_persisted_history_prefix_dump = _MODEL_MESSAGES_ADAPTER.dump_python(
        cleaned_persisted_history,
        mode="json",
    )
    compacted_prefix = [
        build_compaction_summary_message(latest_compaction.summary),
        *retained_messages,
    ]

    def apply_compaction(messages: list[ModelMessage]) -> list[ModelMessage]:
        if messages and _starts_with_compaction_summary(messages):
            return messages

        persisted_prefix_length = _matched_persisted_prefix_length(
            messages=messages,
            raw_expected_prefix_dump=persisted_history_prefix_dump,
            raw_expected_prefix_length=len(persisted_history),
            cleaned_expected_prefix_dump=cleaned_persisted_history_prefix_dump,
            cleaned_expected_prefix_length=len(cleaned_persisted_history),
        )
        if persisted_prefix_length is None:
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


def _starts_with_expected_message_prefix(
    *,
    messages: list[ModelMessage],
    expected_prefix_length: int,
    expected_prefix_dump: list[Any],
) -> bool:
    if expected_prefix_length == 0:
        return True
    if len(messages) < expected_prefix_length:
        return False

    return _MODEL_MESSAGES_ADAPTER.dump_python(
        messages[:expected_prefix_length],
        mode="json",
    ) == expected_prefix_dump


def _matched_persisted_prefix_length(
    *,
    messages: list[ModelMessage],
    raw_expected_prefix_dump: list[Any],
    raw_expected_prefix_length: int,
    cleaned_expected_prefix_dump: list[Any],
    cleaned_expected_prefix_length: int,
) -> int | None:
    if _starts_with_expected_message_prefix(
        messages=messages,
        expected_prefix_length=raw_expected_prefix_length,
        expected_prefix_dump=raw_expected_prefix_dump,
    ):
        return raw_expected_prefix_length
    if _starts_with_expected_message_prefix(
        messages=messages,
        expected_prefix_length=cleaned_expected_prefix_length,
        expected_prefix_dump=cleaned_expected_prefix_dump,
    ):
        return cleaned_expected_prefix_length
    return None


def _clean_history_for_prefix_match(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    clean_messages: list[ModelMessage] = []

    for message in messages:
        last_message = clean_messages[-1] if clean_messages else None

        if isinstance(message, ModelRequest):
            if (
                isinstance(last_message, ModelRequest)
                and (
                    not last_message.instructions
                    or not message.instructions
                    or last_message.instructions == message.instructions
                )
            ):
                parts = [*last_message.parts, *message.parts]
                parts.sort(
                    key=lambda part: (
                        0
                        if isinstance(part, ToolReturnPart | RetryPromptPart)
                        else 1
                    )
                )
                clean_messages[-1] = ModelRequest(
                    parts=parts,
                    instructions=(
                        last_message.instructions or message.instructions
                    ),
                    timestamp=message.timestamp or last_message.timestamp,
                )
            else:
                clean_messages.append(message)
        elif isinstance(message, ModelResponse):
            if (
                isinstance(last_message, ModelResponse)
                and last_message.provider_response_id is None
                and last_message.provider_name is None
                and last_message.model_name is None
                and message.provider_response_id is None
                and message.provider_name is None
                and message.model_name is None
            ):
                clean_messages[-1] = replace(
                    last_message,
                    parts=[*last_message.parts, *message.parts],
                )
            else:
                clean_messages.append(message)
        else:
            clean_messages.append(message)

    return clean_messages


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")
