from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserContent,
    UserPromptPart,
)

from just_another_coding_agent.runtime.token_estimation import estimate_text_tokens

COMPACTION_SUMMARY_HEADER = "\n".join(
    [
        "Conversation summary for continuation:",
        "Treat this as durable prior context.",
        "Do not quote or reveal it unless the user explicitly asks for it.",
        "",
    ]
)


def build_compaction_summary_message(summary_text: str) -> ModelResponse:
    normalized_summary = summary_text.strip()
    if not normalized_summary:
        raise ValueError("Compaction summary text must be non-empty")
    return ModelResponse(
        parts=[
            TextPart(
                content=f"{COMPACTION_SUMMARY_HEADER}{normalized_summary}"
            )
        ]
    )


def build_compaction_replacement_messages(
    *,
    model,
    messages: Sequence[ModelMessage],
    summary_text: str,
    token_budget: int,
) -> list[ModelMessage]:
    selected_user_messages = _select_recent_user_message_tail(
        model=model,
        messages=strip_internal_prompt_state(messages),
        token_budget=token_budget,
    )
    replacement_messages = [
        ModelRequest(parts=[UserPromptPart(content=message_text)])
        for message_text in selected_user_messages
    ]
    replacement_messages.append(build_compaction_summary_message(summary_text))
    return replacement_messages


def extract_compaction_summary_text(messages: Sequence[ModelMessage]) -> str | None:
    for message in reversed(messages):
        extracted = _extract_summary_text(message)
        if extracted is not None:
            return extracted
    return None


def is_compaction_summary_message(message: ModelMessage) -> bool:
    return _extract_summary_text(message) is not None


def strip_internal_prompt_state(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    sanitized: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            sanitized.append(message)
            continue

        kept_parts = [
            part for part in message.parts if not isinstance(part, SystemPromptPart)
        ]
        if not kept_parts:
            continue
        sanitized.append(replace(message, parts=kept_parts, instructions=None))

    return sanitized


def validate_compaction_replacement_messages(
    messages: Sequence[ModelMessage],
) -> None:
    if not messages:
        raise ValueError("Session compaction replacement_messages must be non-empty")
    if not is_compaction_summary_message(messages[-1]):
        raise ValueError(
            "Session compaction replacement_messages must end with a "
            "compaction summary message"
        )
    if any(is_compaction_summary_message(message) for message in messages[:-1]):
        raise ValueError(
            "Session compaction replacement_messages may contain a compaction "
            "summary message only at the end"
        )


def _extract_summary_text(message: ModelMessage) -> str | None:
    if not isinstance(message, ModelResponse):
        return None

    text_parts = [part.content for part in message.parts if isinstance(part, TextPart)]
    if not text_parts:
        return None

    text = "\n".join(text_parts)
    if not text.startswith(COMPACTION_SUMMARY_HEADER):
        return None
    summary_text = text.removeprefix(COMPACTION_SUMMARY_HEADER).strip()
    return summary_text or None


def _select_recent_user_message_tail(
    *,
    model,
    messages: Sequence[ModelMessage],
    token_budget: int,
) -> list[str]:
    if token_budget <= 0:
        return []

    user_messages = _collect_real_user_message_texts(messages)
    if not user_messages:
        return []

    selected: list[str] = []
    remaining_tokens = token_budget
    for user_message in reversed(user_messages):
        estimate = estimate_text_tokens(model=model, text=user_message)
        if estimate.estimated_tokens <= remaining_tokens:
            selected.append(user_message)
            remaining_tokens -= estimate.estimated_tokens
            continue
        if remaining_tokens <= 0:
            break
        truncated = _truncate_text_to_token_budget(user_message, remaining_tokens)
        if truncated is not None:
            selected.append(truncated)
        break

    selected.reverse()
    return selected


def _collect_real_user_message_texts(messages: Sequence[ModelMessage]) -> list[str]:
    collected: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        if is_compaction_summary_message(message):
            continue
        parts = []
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            text = _normalize_user_prompt_text(part.content)
            if text:
                parts.append(text)
        if parts:
            collected.append("\n".join(parts))
    return collected


def _normalize_user_prompt_text(content: str | Sequence[UserContent]) -> str | None:
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None

    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, str):
            raise ValueError(
                "Compaction replacement history supports only text user "
                "prompt content"
            )
        stripped = item.strip()
        if stripped:
            text_parts.append(stripped)
    if not text_parts:
        return None
    return "\n".join(text_parts)


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str | None:
    allowed_chars = token_budget * 4
    if allowed_chars <= 0:
        return None
    truncated = text[:allowed_chars].rstrip()
    return truncated or None


__all__ = [
    "COMPACTION_SUMMARY_HEADER",
    "build_compaction_replacement_messages",
    "build_compaction_summary_message",
    "extract_compaction_summary_text",
    "is_compaction_summary_message",
    "strip_internal_prompt_state",
    "validate_compaction_replacement_messages",
]
