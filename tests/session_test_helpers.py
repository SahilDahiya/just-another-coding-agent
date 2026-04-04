from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, UserPromptPart

from just_another_coding_agent.session.replacement_history import (
    build_compaction_summary_message,
)

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


def all_parts(messages: list[ModelMessage]):
    for message in messages:
        yield from message.parts


def user_prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in all_parts(messages)
        if isinstance(part, UserPromptPart)
    ]


def message_shapes(messages: list[ModelMessage]) -> list[str]:
    return [
        f"{type(message).__name__}:{[type(part).__name__ for part in message.parts]}"
        for message in messages
    ]


def compaction_entry_payload(
    *,
    compacted_through_run_id: str,
    replacement_messages: list[ModelMessage] | None = None,
    summary_text: str = "summary",
) -> dict[str, object]:
    resolved_replacement_messages = (
        replacement_messages
        if replacement_messages is not None
        else [build_compaction_summary_message(summary_text)]
    )
    return {
        "type": "session_compaction",
        "compaction_id": "compact-1",
        "compacted_through_run_id": compacted_through_run_id,
        "replacement_messages": _MODEL_MESSAGES_ADAPTER.dump_python(
            resolved_replacement_messages,
            mode="json",
        ),
    }


_all_parts = all_parts
_user_prompts = user_prompts
_message_shapes = message_shapes
_compaction_entry_payload = compaction_entry_payload
