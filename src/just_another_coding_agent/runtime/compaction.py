from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.models import (
    DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT,
    resolve_canonical_model,
)
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
)

COMPACTION_SUMMARY_DYNAMIC_REF = "session-compaction-summary"
AUTO_COMPACTION_RUN_THRESHOLD = 5
IN_RUN_COMPACTION_SOFT_CHAR_LIMIT = DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT
IN_RUN_COMPACTION_METADATA_KEY = "_jaca_in_run_compaction"
COMPACTION_SUMMARY_INSTRUCTIONS = "\n".join(
    [
        "You summarize coding-agent session state into a structured compaction record.",
        "Preserve only durable information needed to continue the work correctly.",
        "Do not invent facts, files, preferences, or unresolved work.",
        "Prefer short concrete items over verbose prose.",
        "Use current_objective for the active user goal at the compaction boundary.",
        (
            "Use established_facts for confirmed outcomes, code changes, "
            "and verified behavior."
        ),
        "Use user_preferences only for stable user instructions or preferences.",
        (
            "Use important_paths for files or directories that matter to "
            "continuing the work."
        ),
        "Use open_questions for unresolved unknowns or clarification gaps.",
        "Use unresolved_work for concrete next actions that still need to happen.",
        "Return empty lists when a section has nothing durable to keep.",
    ]
)
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

        # PydanticAI passes resumed persisted history in front of any new
        # current-run messages. Match and replace that exact persisted prefix
        # instead of inferring a boundary from run_id shape. Accept both the
        # raw persisted history and the cleaned/merged form PydanticAI may
        # synthesize for consecutive requests.
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


def build_in_run_history_processor(
    *,
    soft_char_limit: int | None = None,
) -> Callable[
    [list[ModelMessage]], list[ModelMessage]
]:
    effective_soft_char_limit = (
        IN_RUN_COMPACTION_SOFT_CHAR_LIMIT
        if soft_char_limit is None
        else soft_char_limit
    )

    def apply_in_run_compaction(messages: list[ModelMessage]) -> list[ModelMessage]:
        current_size = _estimate_message_history_size(messages)
        if current_size <= effective_soft_char_limit:
            return messages

        tool_calls_by_id = _index_tool_calls(messages)
        compacted_messages = list(messages)
        changed = False

        for message_index, message in enumerate(messages):
            if not isinstance(message, ModelRequest):
                continue

            updated_parts = list(message.parts)
            message_changed = False

            for part_index, part in enumerate(message.parts):
                if not isinstance(part, ToolReturnPart):
                    continue
                if _part_has_in_run_compaction(part):
                    continue

                compacted_part = _compact_tool_return_part(
                    part=part,
                    tool_call=tool_calls_by_id.get(part.tool_call_id),
                )
                if compacted_part is None:
                    continue

                updated_parts[part_index] = compacted_part
                message_changed = True
                changed = True

                compacted_messages[message_index] = replace(
                    message,
                    parts=updated_parts,
                )
                current_size = _estimate_message_history_size(compacted_messages)
                if current_size <= effective_soft_char_limit:
                    return compacted_messages

            if message_changed:
                compacted_messages[message_index] = replace(
                    message,
                    parts=updated_parts,
                )

        return compacted_messages if changed else messages

    return apply_in_run_compaction


async def summarize_session_for_compaction(
    *,
    model: Any,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    summarizer = Agent(
        resolve_canonical_model(model),
        output_type=SessionCompactionSummary,
        instructions=COMPACTION_SUMMARY_INSTRUCTIONS,
    )

    @summarizer.output_validator
    def validate_summary(
        summary: SessionCompactionSummary,
    ) -> SessionCompactionSummary:
        normalized = _normalize_compaction_summary(summary)
        if (
            normalized.current_objective is None
            and not normalized.established_facts
            and not normalized.user_preferences
            and not normalized.important_paths
            and not normalized.open_questions
            and not normalized.unresolved_work
        ):
            raise ModelRetry(
                "Compaction summary is empty. Preserve at least one durable "
                "objective, fact, preference, path, question, or unresolved task."
            )

        return normalized

    result = await summarizer.run(_build_compaction_source(loaded_session))
    return result.output


async def summarize_and_append_compaction_to_session(
    *,
    model: Any,
    path,
    workspace_root,
) -> SessionCompactionEntry:
    from just_another_coding_agent.session.jsonl import load_session

    loaded_session = load_session(
        path=path,
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
    )
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    summary = await summarize_session_for_compaction(
        model=model,
        loaded_session=loaded_session,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
    )


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


def restore_in_run_compaction_from_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    restored: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            restored.append(message)
            continue

        updated_parts = list(message.parts)
        changed = False

        for index, part in enumerate(message.parts):
            if not isinstance(part, ToolReturnPart):
                continue

            restored_part = _restore_compacted_tool_return_part(part)
            if restored_part is part:
                continue

            updated_parts[index] = restored_part
            changed = True

        restored.append(replace(message, parts=updated_parts) if changed else message)

    return restored


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


def _part_has_in_run_compaction(part: ToolReturnPart) -> bool:
    return (
        isinstance(part.metadata, dict)
        and IN_RUN_COMPACTION_METADATA_KEY in part.metadata
    )


def _restore_compacted_tool_return_part(part: ToolReturnPart) -> ToolReturnPart:
    if not isinstance(part.metadata, dict):
        return part

    compaction_metadata = part.metadata.get(IN_RUN_COMPACTION_METADATA_KEY)
    if not isinstance(compaction_metadata, dict):
        return part

    if "original_content" not in compaction_metadata:
        raise RuntimeError(
            "In-run compaction metadata is missing original_content"
        )

    restored_metadata = dict(part.metadata)
    restored_content = compaction_metadata["original_content"]
    del restored_metadata[IN_RUN_COMPACTION_METADATA_KEY]
    return replace(
        part,
        content=restored_content,
        metadata=restored_metadata or None,
    )


def _compact_tool_return_part(
    *,
    part: ToolReturnPart,
    tool_call: ToolCallPart | None,
) -> ToolReturnPart | None:
    summary = _build_compacted_tool_return_summary(
        part=part,
        tool_call=tool_call,
    )
    if summary is None or _estimate_json_size(summary) >= _estimate_json_size(
        part.content
    ):
        return None

    if part.metadata is not None and not isinstance(part.metadata, dict):
        return None

    updated_metadata = dict(part.metadata or {})
    updated_metadata[IN_RUN_COMPACTION_METADATA_KEY] = {
        "original_content": part.content,
    }
    return replace(
        part,
        content=summary,
        metadata=updated_metadata,
    )


def _build_compacted_tool_return_summary(
    *,
    part: ToolReturnPart,
    tool_call: ToolCallPart | None,
) -> str | None:
    args = tool_call.args_as_dict() if tool_call is not None else {}

    if part.tool_name == "read" and isinstance(part.content, str):
        path = _string_arg(args, "path")
        return (
            "Compacted historical read result"
            + (f" for {path}" if path is not None else "")
            + f": {_line_count(part.content)} lines, {len(part.content)} chars."
        )

    if part.tool_name == "shell" and isinstance(part.content, dict):
        command = _string_arg(args, "command")
        exit_code = part.content.get("exit_code")
        output = part.content.get("output")
        if isinstance(output, str):
            summary = "Compacted historical shell result"
            if command is not None:
                summary += f" for `{_truncate_shell_label(command)}`"
            summary += (
                f": exit_code={exit_code}, {_line_count(output)} lines, "
                f"{len(output)} chars of output."
            )
            return summary

    if isinstance(part.content, str):
        return (
            f"Compacted historical {part.tool_name} result: "
            f"{_line_count(part.content)} lines, {len(part.content)} chars."
        )

    if isinstance(part.content, dict):
        error_type = part.content.get("error_type")
        message = part.content.get("message")
        if isinstance(error_type, str) and isinstance(message, str):
            return (
                f"Compacted historical {part.tool_name} result: "
                f"{error_type}: {message}"
            )
        return (
            f"Compacted historical {part.tool_name} result: "
            f"{_estimate_json_size(part.content)} chars of structured output."
        )

    return None


def _estimate_message_history_size(messages: list[ModelMessage]) -> int:
    return _estimate_json_size(
        _MODEL_MESSAGES_ADAPTER.dump_python(messages, mode="json")
    )


def _estimate_json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _index_tool_calls(messages: list[ModelMessage]) -> dict[str, ToolCallPart]:
    indexed: dict[str, ToolCallPart] = {}

    for message in messages:
        if not isinstance(message, ModelResponse):
            continue

        for part in message.parts:
            if isinstance(part, ToolCallPart):
                indexed[part.tool_call_id] = part

    return indexed


def _string_arg(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) and value else None


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _truncate_shell_label(command: str, *, limit: int = 56) -> str:
    normalized = " ".join(command.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


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


def _runs_since_latest_compaction(loaded_session: LoadedSession) -> int:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return len(loaded_session.runs)

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return len(loaded_session.runs[summary_run_index + 1 :])


def _build_compaction_source(loaded_session: LoadedSession) -> str:
    latest_compaction = loaded_session.latest_compaction
    start_index = 0
    sections: list[str] = []

    if latest_compaction is not None:
        sections.append("Previous compaction summary:")
        sections.append(_render_summary(latest_compaction.summary))
        start_index = _run_index_for_id(
            loaded_session,
            latest_compaction.summarized_through_run_id,
        ) + 1

    sections.append("Runs since the latest compaction boundary:")
    if start_index >= len(loaded_session.runs):
        sections.append("(no new runs)")
    else:
        for run in loaded_session.runs[start_index:]:
            sections.append(_render_run(run))

    return "\n\n".join(sections)


def _render_summary(summary: SessionCompactionSummary) -> str:
    lines: list[str] = []
    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")
    _append_rendered_section(lines, "Established facts", summary.established_facts)
    _append_rendered_section(lines, "User preferences", summary.user_preferences)
    _append_rendered_section(lines, "Important paths", summary.important_paths)
    _append_rendered_section(lines, "Open questions", summary.open_questions)
    _append_rendered_section(lines, "Unresolved work", summary.unresolved_work)
    return "\n".join(lines) if lines else "(empty summary)"


def _append_rendered_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)


def _render_run(run) -> str:
    lines = [f"Run {run.run_id}", f"Prompt: {run.prompt}"]
    if run.thinking is not None:
        lines.append(f"Thinking: {run.thinking}")

    lines.append("Messages:")
    for message in run.messages:
        lines.extend(f"- {line}" for line in _render_message(message))

    lines.append("Events:")
    for event in run.events:
        lines.append(f"- {event.type}: {event.model_dump_json()}")

    return "\n".join(lines)


def _normalize_compaction_summary(
    summary: SessionCompactionSummary,
) -> SessionCompactionSummary:
    current_objective = _normalize_optional_text(summary.current_objective)
    return SessionCompactionSummary(
        current_objective=current_objective,
        established_facts=_normalize_summary_items(summary.established_facts),
        user_preferences=_normalize_summary_items(summary.user_preferences),
        important_paths=_normalize_summary_items(summary.important_paths),
        open_questions=_normalize_summary_items(summary.open_questions),
        unresolved_work=_normalize_summary_items(summary.unresolved_work),
    )


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_summary_items(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _render_message(message: ModelMessage) -> list[str]:
    rendered_parts: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            rendered_parts.append(f"user: {part.content}")
        elif isinstance(part, SystemPromptPart):
            rendered_parts.append(f"system: {part.content}")
        elif isinstance(part, TextPart):
            rendered_parts.append(f"assistant: {part.content}")
        elif isinstance(part, ThinkingPart):
            rendered_parts.append(f"assistant_thinking: {part.content}")
        elif isinstance(part, ToolCallPart):
            rendered_parts.append(
                f"tool_call {part.tool_name}: {part.args_as_json_str()}"
            )
        elif isinstance(part, ToolReturnPart):
            rendered_parts.append(
                f"tool_return {part.tool_name}: {part.model_response_str()}"
            )
        else:
            raise RuntimeError(
                "Unsupported message part for compaction: "
                f"{type(part).__name__}"
            )

    return rendered_parts


__all__ = [
    "AUTO_COMPACTION_RUN_THRESHOLD",
    "COMPACTION_SUMMARY_DYNAMIC_REF",
    "COMPACTION_SUMMARY_INSTRUCTIONS",
    "IN_RUN_COMPACTION_SOFT_CHAR_LIMIT",
    "build_compaction_summary_message",
    "build_in_run_history_processor",
    "build_session_history_processor",
    "restore_in_run_compaction_from_messages",
    "summarize_and_append_compaction_to_session",
    "summarize_session_for_compaction",
    "should_auto_compact_session",
    "strip_compaction_summary_from_messages",
]
