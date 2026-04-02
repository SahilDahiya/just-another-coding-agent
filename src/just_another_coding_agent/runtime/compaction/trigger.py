from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse

from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction,
)
from just_another_coding_agent.runtime.compaction.budget import (
    build_effective_compaction_context_window_tokens,
)
from just_another_coding_agent.runtime.compaction.constants import (
    SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION,
    SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
    SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
)
from just_another_coding_agent.runtime.compaction.resume import (
    build_resume_message_history,
)
from just_another_coding_agent.runtime.models import get_model_context_window_tokens

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


def should_auto_compact_session(
    loaded_session: LoadedSession,
    *,
    model: Any,
    get_context_window_tokens: Callable[[Any], int | None] = (
        get_model_context_window_tokens
    ),
) -> bool:
    if not loaded_session.runs:
        return False

    context_window_tokens = get_context_window_tokens(model)
    if context_window_tokens is None:
        return False

    effective_context_window_tokens = build_effective_compaction_context_window_tokens(
        context_window_tokens
    )
    estimated_resume_history_tokens = _estimate_resume_history_tokens(loaded_session)
    compaction_trigger_budget_tokens = int(
        effective_context_window_tokens
        * SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION
    )
    if (
        estimated_resume_history_tokens + SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS
        < compaction_trigger_budget_tokens
    ):
        return False

    if runs_since_latest_compaction(loaded_session) == 0:
        return False

    return True


def _estimate_resume_history_tokens(loaded_session: LoadedSession) -> int:
    resume_history = build_resume_message_history(loaded_session)
    measured_usage = _find_last_measured_usage_tokens(resume_history)
    if measured_usage is not None:
        usage_tokens, last_usage_index = measured_usage
        trailing_messages = resume_history[last_usage_index + 1 :]
        return usage_tokens + math.ceil(
            _estimate_message_history_chars(trailing_messages)
            / SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        )

    return math.ceil(
        _estimate_message_history_chars(resume_history)
        / SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
    )


def _find_last_measured_usage_tokens(
    messages: list[ModelMessage],
) -> tuple[int, int] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        input_tokens = usage.input_tokens if usage is not None else None
        if input_tokens is None or input_tokens <= 0:
            continue
        return input_tokens, index
    return None


def _estimate_message_history_chars(messages: list[ModelMessage]) -> int:
    return len(
        json.dumps(
            _MODEL_MESSAGES_ADAPTER.dump_python(messages, mode="json"),
            ensure_ascii=False,
        )
    )


__all__ = ["should_auto_compact_session"]
