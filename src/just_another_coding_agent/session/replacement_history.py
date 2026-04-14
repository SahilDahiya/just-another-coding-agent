from __future__ import annotations

from collections.abc import Mapping, Sequence
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
        parts=[TextPart(content=f"{COMPACTION_SUMMARY_HEADER}{normalized_summary}")]
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
        truncated = truncate_middle_to_token_budget(
            user_message, remaining_tokens
        )
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
                "Compaction replacement history supports only text user prompt content"
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


def strip_failed_correction_tail(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    sanitized = list(messages)

    while sanitized:
        last_message = sanitized[-1]
        if not isinstance(last_message, ModelRequest):
            break

        retry_parts = [
            part for part in last_message.parts if isinstance(part, RetryPromptPart)
        ]
        if not retry_parts or len(retry_parts) != len(last_message.parts):
            break

        retry_tool_call_ids = {part.tool_call_id for part in retry_parts}
        sanitized.pop()

        if not sanitized:
            break

        previous_message = sanitized[-1]
        if not isinstance(previous_message, ModelResponse):
            break

        kept_parts = [
            part
            for part in previous_message.parts
            if not (
                isinstance(part, ToolCallPart)
                and part.tool_call_id in retry_tool_call_ids
            )
        ]

        if not kept_parts:
            sanitized.pop()
            continue

        if len(kept_parts) != len(previous_message.parts):
            sanitized[-1] = replace(previous_message, parts=kept_parts)

    return sanitized


def sanitize_failed_run_messages(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    # Canonical runtime/session histories reach this layer with ordered
    # ToolCallPart/ToolReturnPart pairs, and failed-correction RetryPromptPart
    # tails are removed separately below. On that input, strip_unpaired_tool_parts
    # is equivalent to the older pending-id sweep that lived in run.py and
    # subagent.py, without re-encoding that broader malformed-history repair.
    return strip_failed_correction_tail(strip_unpaired_tool_parts(messages))


def _is_real_user_prompt_message(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in message.parts)


def _user_prompt_texts(message: ModelMessage) -> list[str]:
    if not isinstance(message, ModelRequest):
        return []
    texts: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            content = part.content
            if isinstance(content, str):
                texts.append(content)
    return texts


def reconcile_synthetic_prompt_counts(
    counts: Mapping[str, int],
    messages: Sequence[ModelMessage],
) -> dict[str, int]:
    """Cap each tracked synthetic count at the number of actual occurrences
    in the given history.

    Called after any mutation that REMOVES messages from the live history
    (in-run compaction). A synthetic prompt that is no longer present in the
    history must have its count dropped, otherwise a later real user prompt
    with the same text would be consumed as synthetic by
    build_in_run_truncated_history.
    """
    if not counts:
        return {}
    occurrences: dict[str, int] = {}
    for message in messages:
        for text in _user_prompt_texts(message):
            if text in counts:
                occurrences[text] = occurrences.get(text, 0) + 1
    reconciled: dict[str, int] = {}
    for text, tracked in counts.items():
        live = occurrences.get(text, 0)
        capped = min(tracked, live)
        if capped > 0:
            reconciled[text] = capped
    return reconciled


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


def _message_is_synthetic_user_prompt(
    message: ModelMessage,
    remaining_synthetic_counts: dict[str, int],
) -> bool:
    """Consume a matching synthetic occurrence from the multiset if present.

    Returns True if this message is a UserPromptPart that matches a pending
    synthetic text (and decrements the counter). The first N textually-matching
    UserPromptParts encountered (in history order) are classified as synthetic;
    additional matches beyond the counter are treated as real user prompts
    that happen to share the same text.
    """
    if not isinstance(message, ModelRequest):
        return False
    for part in message.parts:
        if not isinstance(part, UserPromptPart):
            continue
        content = part.content
        if not isinstance(content, str):
            continue
        if remaining_synthetic_counts.get(content, 0) > 0:
            remaining_synthetic_counts[content] -= 1
            if remaining_synthetic_counts[content] == 0:
                del remaining_synthetic_counts[content]
            return True
    return False


def _estimate_message_tokens(message: ModelMessage, *, model) -> int:
    text = _message_text_for_budget(message)
    return estimate_text_tokens(model=model, text=text).estimated_tokens


def build_in_run_truncated_history(
    *,
    messages: Sequence[ModelMessage],
    model,
    token_budget: int,
    synthetic_prompt_counts: Mapping[str, int] | None = None,
) -> list[ModelMessage]:
    sanitized = strip_thinking_parts(messages)
    if not sanitized:
        return []

    # Phase 1: identify anchor indices that must always be preserved.
    #
    # Anchors:
    # - Real user prompts (original task and any mid-run steers) — the model
    #   needs these to understand intent. Synthetic prompts generated by the
    #   run loop (compaction continuation, correction retries) are excluded
    #   via a multiset of known synthetic texts; the first N matching
    #   occurrences in history order are classified as synthetic (decrementing
    #   the counter), and additional matches beyond the recorded count are
    #   treated as real user prompts that happen to share the same text.
    # - Durable compaction summary messages — carried across prior compactions
    #   and must not be dropped on a subsequent in-run compaction.
    #
    # Anchors may appear anywhere in the history, not just as leading messages,
    # because prior in-run compactions can put injected context (project docs,
    # runtime frame) ahead of the original user turn.
    remaining_synthetic_counts: dict[str, int] = (
        dict(synthetic_prompt_counts) if synthetic_prompt_counts else {}
    )
    anchor_indices: set[int] = set()
    for idx, message in enumerate(sanitized):
        if _message_is_synthetic_user_prompt(message, remaining_synthetic_counts):
            continue
        if _is_real_user_prompt_message(message) or is_compaction_summary_message(
            message
        ):
            anchor_indices.add(idx)

    selected: set[int] = set(anchor_indices)
    budget_used = sum(
        _estimate_message_tokens(sanitized[i], model=model) for i in anchor_indices
    )

    # Phase 2: walk from newest to oldest, filling remaining budget with
    # recent tool rounds / assistant text. Always keep at least one non-anchor
    # message so the model has current state to continue from (unless there
    # are no non-anchor messages to choose).
    non_anchor_kept = 0
    for idx in range(len(sanitized) - 1, -1, -1):
        if idx in selected:
            continue
        cost = _estimate_message_tokens(sanitized[idx], model=model)
        if (
            token_budget > 0
            and budget_used + cost > token_budget
            and non_anchor_kept > 0
        ):
            break
        selected.add(idx)
        budget_used += cost
        non_anchor_kept += 1

    # Guarantee at least one message survives so callers always get a
    # non-empty history.
    if not selected:
        selected.add(len(sanitized) - 1)

    result = [sanitized[i] for i in sorted(selected)]
    return strip_unpaired_tool_parts(result)


CHARS_PER_TOKEN_HEURISTIC = 4
TRUNCATION_MARKER_TEMPLATE = "\n\n[…approximately {} tokens truncated…]\n\n"


def truncate_middle_to_token_budget(text: str, token_budget: int) -> str | None:
    if token_budget <= 0:
        return None

    max_chars = token_budget * CHARS_PER_TOKEN_HEURISTIC
    if len(text) <= max_chars:
        return text

    removed_chars = len(text) - max_chars
    removed_tokens = (
        removed_chars + CHARS_PER_TOKEN_HEURISTIC - 1
    ) // CHARS_PER_TOKEN_HEURISTIC
    marker = TRUNCATION_MARKER_TEMPLATE.format(removed_tokens)

    budget_for_content = max(max_chars - len(marker), 0)
    left_budget = budget_for_content // 2
    right_budget = budget_for_content - left_budget

    left = text[:left_budget]
    right = text[len(text) - right_budget :] if right_budget > 0 else ""

    result = f"{left}{marker}{right}".strip()
    return result or None


__all__ = [
    "COMPACTION_SUMMARY_HEADER",
    "build_compaction_replacement_messages",
    "build_compaction_summary_message",
    "build_in_run_truncated_history",
    "extract_compaction_summary_text",
    "is_compaction_summary_message",
    "reconcile_synthetic_prompt_counts",
    "sanitize_failed_run_messages",
    "strip_failed_correction_tail",
    "strip_internal_prompt_state",
    "strip_thinking_parts",
    "strip_unpaired_tool_parts",
    "truncate_middle_to_token_budget",
    "validate_compaction_replacement_messages",
]
