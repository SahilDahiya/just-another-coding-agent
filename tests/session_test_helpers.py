from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, UserPromptPart

from just_another_coding_agent.contracts.session import SessionCompactionSummary

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
    summarized_through_run_id: str,
    summary: SessionCompactionSummary,
    first_kept_run_id: str | None = None,
    checkpoint_through_run_id: str,
    checkpoint_messages: list[ModelMessage] | None = None,
) -> dict[str, object]:
    return {
        "type": "session_compaction",
        "compaction_id": "compact-1",
        "summarized_through_run_id": summarized_through_run_id,
        "first_kept_run_id": first_kept_run_id,
        "checkpoint_through_run_id": checkpoint_through_run_id,
        "checkpoint_messages": _MODEL_MESSAGES_ADAPTER.dump_python(
            checkpoint_messages if checkpoint_messages is not None else [],
            mode="json",
        ),
        "summary": summary.model_dump(mode="json"),
    }


_all_parts = all_parts
_user_prompts = user_prompts
_message_shapes = message_shapes
_compaction_entry_payload = compaction_entry_payload
