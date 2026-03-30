from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
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

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


def build_in_run_history_processor(
    *,
    soft_char_limit: int | None = None,
) -> Callable[[list[ModelMessage]], Awaitable[list[ModelMessage]]]:
    effective_soft_char_limit = (
        IN_RUN_COMPACTION_SOFT_CHAR_LIMIT
        if soft_char_limit is None
        else soft_char_limit
    )

    async def apply_in_run_compaction(
        messages: list[ModelMessage],
    ) -> list[ModelMessage]:
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
        raise RuntimeError("In-run compaction metadata is missing original_content")

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


def _truncate_shell_label(command: str, *, limit: int = 56) -> str:
    normalized = " ".join(command.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
