from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from just_another_coding_agent.runtime.models import (
    DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT,
)

IN_RUN_COMPACTION_SOFT_CHAR_LIMIT = DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT
IN_RUN_COMPACTION_METADATA_KEY = "_jaca_in_run_compaction"
IN_RUN_COMPACTION_PRESERVED_TAIL_MESSAGES = 2

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


@dataclass(frozen=True)
class InRunCompactionResult:
    messages: list[ModelMessage]
    was_compacted: bool
    original_size_chars: int
    compacted_size_chars: int
    compacted_tool_result_count: int
    preserved_tail_start: int
    used_full_history_fallback: bool


@dataclass
class InRunCompactionController:
    soft_char_limit: int
    preserved_tail_messages: int = IN_RUN_COMPACTION_PRESERVED_TAIL_MESSAGES
    _original_content_by_storage_key: dict[str, Any] = field(default_factory=dict)

    async def apply(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        return compact_in_run_message_history(
            messages,
            soft_char_limit=self.soft_char_limit,
            preserved_tail_messages=self.preserved_tail_messages,
            original_content_store=self._original_content_by_storage_key,
        ).messages

    def restore(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        return restore_in_run_compaction_from_messages(
            messages,
            original_content_store=self._original_content_by_storage_key,
        )


def build_in_run_history_processor(
    *,
    soft_char_limit: int | None = None,
) -> Callable[[list[ModelMessage]], Awaitable[list[ModelMessage]]]:
    return build_in_run_compaction_controller(
        soft_char_limit=soft_char_limit
    ).apply


def build_in_run_compaction_controller(
    *,
    soft_char_limit: int | None = None,
    preserved_tail_messages: int = IN_RUN_COMPACTION_PRESERVED_TAIL_MESSAGES,
) -> InRunCompactionController:
    effective_soft_char_limit = (
        IN_RUN_COMPACTION_SOFT_CHAR_LIMIT
        if soft_char_limit is None
        else soft_char_limit
    )
    return InRunCompactionController(
        soft_char_limit=effective_soft_char_limit,
        preserved_tail_messages=preserved_tail_messages,
    )


def compact_in_run_message_history(
    messages: list[ModelMessage],
    *,
    soft_char_limit: int,
    preserved_tail_messages: int = IN_RUN_COMPACTION_PRESERVED_TAIL_MESSAGES,
    original_content_store: dict[str, Any] | None = None,
) -> InRunCompactionResult:
    original_size = _estimate_message_history_size(messages)
    if original_size <= soft_char_limit:
        return InRunCompactionResult(
            messages=messages,
            was_compacted=False,
            original_size_chars=original_size,
            compacted_size_chars=original_size,
            compacted_tool_result_count=0,
            preserved_tail_start=len(messages),
            used_full_history_fallback=False,
        )

    tool_calls_by_id = _index_tool_calls(messages)
    preserved_tail_start = _preserved_tail_start(
        messages,
        preserved_tail_messages=preserved_tail_messages,
    )
    prefix_messages = messages[:preserved_tail_start]
    preserved_tail = messages[preserved_tail_start:]

    compacted_prefix, compacted_prefix_count = _compact_messages_until_limit(
        prefix_messages,
        tool_calls_by_id=tool_calls_by_id,
        soft_char_limit=soft_char_limit,
        trailing_messages=preserved_tail,
        original_content_store=original_content_store,
    )
    compacted_messages = [*compacted_prefix, *preserved_tail]
    compacted_size = _estimate_message_history_size(compacted_messages)
    if compacted_size <= soft_char_limit:
        return InRunCompactionResult(
            messages=compacted_messages,
            was_compacted=compacted_prefix_count > 0,
            original_size_chars=original_size,
            compacted_size_chars=compacted_size,
            compacted_tool_result_count=compacted_prefix_count,
            preserved_tail_start=preserved_tail_start,
            used_full_history_fallback=False,
        )

    (
        fully_compacted_messages,
        full_history_compacted_count,
    ) = _compact_messages_until_limit(
        compacted_messages,
        tool_calls_by_id=tool_calls_by_id,
        soft_char_limit=soft_char_limit,
        trailing_messages=[],
        original_content_store=original_content_store,
    )
    fully_compacted_size = _estimate_message_history_size(fully_compacted_messages)
    return InRunCompactionResult(
        messages=fully_compacted_messages,
        was_compacted=(compacted_prefix_count + full_history_compacted_count) > 0,
        original_size_chars=original_size,
        compacted_size_chars=fully_compacted_size,
        compacted_tool_result_count=(
            compacted_prefix_count + full_history_compacted_count
        ),
        preserved_tail_start=preserved_tail_start,
        used_full_history_fallback=True,
    )


def restore_in_run_compaction_from_messages(
    messages: list[ModelMessage],
    *,
    original_content_store: dict[str, Any] | None = None,
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

            restored_part = _restore_compacted_tool_return_part(
                part,
                original_content_store=original_content_store,
            )
            if restored_part is part:
                continue

            updated_parts[index] = restored_part
            changed = True

        restored.append(replace(message, parts=updated_parts) if changed else message)

    return restored


def _part_has_in_run_compaction(part: ToolReturnPart) -> bool:
    return (
        isinstance(part.metadata, dict)
        and IN_RUN_COMPACTION_METADATA_KEY in part.metadata
    )


def _restore_compacted_tool_return_part(
    part: ToolReturnPart,
    *,
    original_content_store: dict[str, Any] | None,
) -> ToolReturnPart:
    if not isinstance(part.metadata, dict):
        return part

    compaction_metadata = part.metadata.get(IN_RUN_COMPACTION_METADATA_KEY)
    if not isinstance(compaction_metadata, dict):
        return part

    storage_key = compaction_metadata.get("storage_key")
    if not isinstance(storage_key, str) or not storage_key:
        raise RuntimeError("In-run compaction metadata is missing storage_key")
    if original_content_store is None:
        raise RuntimeError("In-run compaction restore requires original content state")
    if storage_key not in original_content_store:
        raise RuntimeError(
            f"In-run compaction original content is missing for {storage_key!r}"
        )

    restored_metadata = dict(part.metadata)
    restored_content = original_content_store[storage_key]
    del restored_metadata[IN_RUN_COMPACTION_METADATA_KEY]
    return replace(
        part,
        content=restored_content,
        metadata=restored_metadata or None,
    )


def _preserved_tail_start(
    messages: list[ModelMessage],
    *,
    preserved_tail_messages: int,
) -> int:
    if preserved_tail_messages <= 0:
        return len(messages)
    start = max(0, len(messages) - preserved_tail_messages)
    while start > 0 and _message_requires_previous_tool_call(messages[start]):
        start -= 1
    return start


def _message_requires_previous_tool_call(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, ToolReturnPart) for part in message.parts)


def _compact_messages_until_limit(
    messages: list[ModelMessage],
    *,
    tool_calls_by_id: dict[str, ToolCallPart],
    soft_char_limit: int,
    trailing_messages: list[ModelMessage],
    original_content_store: dict[str, Any] | None,
) -> tuple[list[ModelMessage], int]:
    compacted_messages = list(messages)
    compacted_tool_result_count = 0
    current_size = _estimate_message_history_size(
        [*compacted_messages, *trailing_messages]
    )
    if current_size <= soft_char_limit:
        return compacted_messages, compacted_tool_result_count

    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue

        updated_parts = list(compacted_messages[message_index].parts)
        message_changed = False

        for part_index, part in enumerate(updated_parts):
            if not isinstance(part, ToolReturnPart):
                continue
            if _part_has_in_run_compaction(part):
                continue

            compacted_part = _compact_tool_return_part(
                part=part,
                tool_call=tool_calls_by_id.get(part.tool_call_id),
                original_content_store=original_content_store,
            )
            if compacted_part is None:
                continue

            updated_parts[part_index] = compacted_part
            message_changed = True
            compacted_tool_result_count += 1
            compacted_messages[message_index] = replace(
                compacted_messages[message_index],
                parts=updated_parts,
            )
            current_size = _estimate_message_history_size(
                [*compacted_messages, *trailing_messages]
            )
            if current_size <= soft_char_limit:
                return compacted_messages, compacted_tool_result_count

        if message_changed:
            compacted_messages[message_index] = replace(
                compacted_messages[message_index],
                parts=updated_parts,
            )

    return compacted_messages, compacted_tool_result_count


def _compact_tool_return_part(
    *,
    part: ToolReturnPart,
    tool_call: ToolCallPart | None,
    original_content_store: dict[str, Any] | None,
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

    storage_key = part.tool_call_id
    if original_content_store is not None:
        original_content_store.setdefault(storage_key, part.content)

    updated_metadata = dict(part.metadata or {})
    updated_metadata[IN_RUN_COMPACTION_METADATA_KEY] = {
        "storage_key": storage_key,
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
                f"Compacted historical {part.tool_name} result: {error_type}: {message}"
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


__all__ = [
    "IN_RUN_COMPACTION_METADATA_KEY",
    "IN_RUN_COMPACTION_PRESERVED_TAIL_MESSAGES",
    "IN_RUN_COMPACTION_SOFT_CHAR_LIMIT",
    "InRunCompactionController",
    "InRunCompactionResult",
    "build_in_run_compaction_controller",
    "build_in_run_history_processor",
    "compact_in_run_message_history",
    "restore_in_run_compaction_from_messages",
]


def _truncate_shell_label(command: str, *, limit: int = 56) -> str:
    normalized = " ".join(command.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
