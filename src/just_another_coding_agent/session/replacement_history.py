from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

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


def strip_unpaired_tool_parts(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    call_ids: set[str] = set()
    return_ids: set[str] = set()

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                call_ids.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                return_ids.add(part.tool_call_id)

    unpaired_ids = (call_ids - return_ids) | (return_ids - call_ids)
    if not unpaired_ids:
        return list(messages)

    sanitized: list[ModelMessage] = []
    for message in messages:
        kept_parts = [
            part
            for part in message.parts
            if not (
                isinstance(part, (ToolCallPart, ToolReturnPart))
                and part.tool_call_id in unpaired_ids
            )
        ]
        if not kept_parts:
            continue
        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue
        sanitized.append(replace(message, parts=kept_parts))

    return sanitized


def strip_thinking_parts(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    sanitized: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            sanitized.append(message)
            continue
        kept_parts = [
            part for part in message.parts if not isinstance(part, ThinkingPart)
        ]
        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue
        if not kept_parts:
            continue
        sanitized.append(replace(message, parts=kept_parts))
    return sanitized


def _is_real_user_prompt_message(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in message.parts)


def _message_text_for_budget(message: ModelMessage) -> str:
    fragments: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            content = part.content
            if isinstance(content, str):
                fragments.append(content)
            else:
                for item in content:
                    if isinstance(item, str):
                        fragments.append(item)
        elif isinstance(part, TextPart):
            fragments.append(part.content)
        elif isinstance(part, ToolCallPart):
            fragments.append(part.tool_name)
            args = part.args
            if isinstance(args, str):
                fragments.append(args)
            elif args is not None:
                import json as _json
                fragments.append(_json.dumps(args, ensure_ascii=False))
        elif isinstance(part, ToolReturnPart):
            fragments.append(part.tool_name)
            content = part.content
            if isinstance(content, str):
                fragments.append(content)
            elif content is not None:
                import json as _json
                fragments.append(_json.dumps(content, ensure_ascii=False))
        elif isinstance(part, RetryPromptPart):
            fragments.append(str(part.content))
    return "\n".join(fragments)


def build_in_run_truncated_history(
    *,
    messages: Sequence[ModelMessage],
    model,
    token_budget: int,
) -> list[ModelMessage]:
    sanitized = strip_thinking_parts(messages)

    prefix_messages: list[ModelMessage] = []
    body_start_index = 0
    for idx, message in enumerate(sanitized):
        if _is_real_user_prompt_message(message) and not is_compaction_summary_message(
            message
        ):
            prefix_messages.append(message)
            body_start_index = idx + 1
            continue
        break

    body_messages = list(sanitized[body_start_index:])

    if token_budget <= 0 or not body_messages:
        return strip_unpaired_tool_parts(prefix_messages)

    selected_tail: list[ModelMessage] = []
    remaining = token_budget
    for message in reversed(body_messages):
        text = _message_text_for_budget(message)
        estimate = estimate_text_tokens(model=model, text=text)
        if estimate.estimated_tokens > remaining and selected_tail:
            break
        selected_tail.append(message)
        remaining = max(remaining - estimate.estimated_tokens, 0)

    selected_tail.reverse()
    return strip_unpaired_tool_parts([*prefix_messages, *selected_tail])


CHARS_PER_TOKEN_HEURISTIC = 4
TRUNCATION_MARKER_TEMPLATE = "\n\n[…approximately {} tokens truncated…]\n\n"


def truncate_middle_to_token_budget(text: str, token_budget: int) -> str | None:
    if token_budget <= 0:
        return None

    max_chars = token_budget * CHARS_PER_TOKEN_HEURISTIC
    if len(text) <= max_chars:
        return text

    removed_chars = len(text) - max_chars
    removed_tokens = (removed_chars + CHARS_PER_TOKEN_HEURISTIC - 1) // CHARS_PER_TOKEN_HEURISTIC
    marker = TRUNCATION_MARKER_TEMPLATE.format(removed_tokens)

    budget_for_content = max(max_chars - len(marker), 0)
    left_budget = budget_for_content // 2
    right_budget = budget_for_content - left_budget

    left = text[:left_budget]
    right = text[len(text) - right_budget :] if right_budget > 0 else ""

    result = f"{left}{marker}{right}".strip()
    return result or None


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str | None:
    return truncate_middle_to_token_budget(text, token_budget)


__all__ = [
    "COMPACTION_SUMMARY_HEADER",
    "build_compaction_replacement_messages",
    "build_compaction_summary_message",
    "build_in_run_truncated_history",
    "extract_compaction_summary_text",
    "is_compaction_summary_message",
    "strip_internal_prompt_state",
    "strip_thinking_parts",
    "strip_unpaired_tool_parts",
    "truncate_middle_to_token_budget",
    "validate_compaction_replacement_messages",
]
