from __future__ import annotations

import json as _json
from collections.abc import Sequence
from typing import Any

import tiktoken
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.runtime.models import get_external_model_id

_OPENAI_PREFIX_TO_ENCODING: tuple[tuple[str, str], ...] = (
    ("gpt-5", "o200k_base"),
    ("gpt-4.1", "o200k_base"),
    ("gpt-4o", "o200k_base"),
    ("o1", "o200k_base"),
    ("o3", "o200k_base"),
    ("o4", "o200k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
)
_DEFAULT_ENCODING = "o200k_base"
_CLAUDE_OVERCOUNT_MULTIPLIER = 1.1
_PER_MESSAGE_FRAMING_TOKENS = 4
_THINKING_PART_PLACEHOLDER_TOKENS = 16


def _model_id_string(model: Any) -> str:
    if model is None:
        return ""
    external = get_external_model_id(model)
    if external:
        return external
    return str(model)


def _is_claude_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return "claude" in lowered


def _encoding_for_model(model: Any) -> tiktoken.Encoding:
    model_id = _model_id_string(model).lower()
    for prefix, encoding_name in _OPENAI_PREFIX_TO_ENCODING:
        if prefix in model_id:
            return tiktoken.get_encoding(encoding_name)
    return tiktoken.get_encoding(_DEFAULT_ENCODING)


def _apply_provider_bias(count: int, model: Any) -> int:
    if _is_claude_model(_model_id_string(model)):
        return int(count * _CLAUDE_OVERCOUNT_MULTIPLIER)
    return count


def count_text_tokens(*, model: Any, text: str | None) -> int:
    if not text:
        return 0
    encoding = _encoding_for_model(model)
    count = len(encoding.encode(text, disallowed_special=()))
    return _apply_provider_bias(count, model)


def _count_json_tokens(*, model: Any, value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return count_text_tokens(model=model, text=value)
    try:
        serialized = _json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(value)
    return count_text_tokens(model=model, text=serialized)


def _count_part_tokens(*, model: Any, part: Any) -> int:
    if isinstance(part, UserPromptPart):
        content = part.content
        if isinstance(content, str):
            return count_text_tokens(model=model, text=content)
        total = 0
        for item in content:
            if isinstance(item, str):
                total += count_text_tokens(model=model, text=item)
        return total
    if isinstance(part, SystemPromptPart):
        return count_text_tokens(model=model, text=part.content)
    if isinstance(part, TextPart):
        return count_text_tokens(model=model, text=part.content)
    if isinstance(part, ToolCallPart):
        return (
            count_text_tokens(model=model, text=part.tool_name)
            + _count_json_tokens(model=model, value=part.args)
        )
    if isinstance(part, ToolReturnPart):
        return (
            count_text_tokens(model=model, text=part.tool_name)
            + _count_json_tokens(model=model, value=part.content)
        )
    if isinstance(part, RetryPromptPart):
        return count_text_tokens(model=model, text=str(part.content))
    if isinstance(part, ThinkingPart):
        content_tokens = count_text_tokens(model=model, text=part.content or "")
        return max(content_tokens, _THINKING_PART_PLACEHOLDER_TOKENS)
    return 0


def count_message_tokens(
    messages: Sequence[ModelMessage],
    *,
    model: Any,
) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, (ModelRequest, ModelResponse)):
            continue
        for part in message.parts:
            total += _count_part_tokens(model=model, part=part)
        total += _PER_MESSAGE_FRAMING_TOKENS
    return total


__all__ = [
    "count_message_tokens",
    "count_text_tokens",
]
