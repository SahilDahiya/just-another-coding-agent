from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction,
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

    estimated_resume_history_tokens = _estimate_resume_history_tokens(loaded_session)
    compaction_trigger_budget_tokens = int(
        context_window_tokens * SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION
    )
    if (
        estimated_resume_history_tokens + SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS
        < compaction_trigger_budget_tokens
    ):
        return False

    latest_compaction = loaded_session.latest_compaction
    if (
        latest_compaction is not None
        and latest_compaction.first_kept_run_id is not None
    ):
        raise RuntimeError(
            "Auto-compaction trigger does not support retained-run "
            "compaction boundaries"
        )

    if runs_since_latest_compaction(loaded_session) == 0:
        return False

    return True


def _estimate_resume_history_tokens(loaded_session: LoadedSession) -> int:
    resume_history = build_resume_message_history(loaded_session)
    return math.ceil(
        _estimate_message_history_chars(resume_history)
        / SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
    )


def _estimate_message_history_chars(messages: list[ModelMessage]) -> int:
    return len(
        json.dumps(
            _MODEL_MESSAGES_ADAPTER.dump_python(messages, mode="json"),
            ensure_ascii=False,
        )
    )


__all__ = ["should_auto_compact_session"]
