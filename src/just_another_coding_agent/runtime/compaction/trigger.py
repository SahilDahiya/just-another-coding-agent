from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse

from just_another_coding_agent.contracts.compaction import (
    COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
    CompactionBudgetReport,
)
from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction,
)
from just_another_coding_agent.runtime.compaction.budget import (
    build_compaction_output_headroom_tokens,
    build_effective_compaction_context_window_tokens,
)
from just_another_coding_agent.runtime.compaction.constants import (
    SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION,
    SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
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
    return build_auto_compact_session_budget_report(
        loaded_session,
        model=model,
        get_context_window_tokens=get_context_window_tokens,
    ).should_compact


def build_auto_compact_session_budget_report(
    loaded_session: LoadedSession,
    *,
    model: Any,
    get_context_window_tokens: Callable[[Any], int | None] = (
        get_model_context_window_tokens
    ),
) -> CompactionBudgetReport:
    runs_since_compaction = runs_since_latest_compaction(loaded_session)
    (
        estimated_resume_history_tokens,
        measured_usage_tokens,
        estimated_trailing_tokens,
    ) = _estimate_resume_history_budget_components(loaded_session)
    estimated_pre_run_tokens = (
        estimated_resume_history_tokens + SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS
    )

    if not loaded_session.runs:
        return CompactionBudgetReport(
            should_compact=False,
            reason="no_runs",
            prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
            estimated_resume_history_tokens=estimated_resume_history_tokens,
            estimated_pre_run_tokens=estimated_pre_run_tokens,
            measured_usage_tokens=measured_usage_tokens,
            estimated_trailing_tokens=estimated_trailing_tokens,
            runs_since_latest_compaction=runs_since_compaction,
        )

    context_window_tokens = get_context_window_tokens(model)
    if context_window_tokens is None:
        return CompactionBudgetReport(
            should_compact=False,
            reason="unknown_context_window",
            prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
            estimated_resume_history_tokens=estimated_resume_history_tokens,
            estimated_pre_run_tokens=estimated_pre_run_tokens,
            measured_usage_tokens=measured_usage_tokens,
            estimated_trailing_tokens=estimated_trailing_tokens,
            runs_since_latest_compaction=runs_since_compaction,
        )

    output_headroom_tokens = build_compaction_output_headroom_tokens(
        context_window_tokens
    )
    effective_context_window_tokens = build_effective_compaction_context_window_tokens(
        context_window_tokens
    )
    compaction_trigger_budget_tokens = math.floor(
        (
            effective_context_window_tokens
            * SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION
        )
        + 1e-6
    )

    if runs_since_compaction == 0:
        return CompactionBudgetReport(
            should_compact=False,
            reason="no_new_work",
            context_window_tokens=context_window_tokens,
            effective_context_window_tokens=effective_context_window_tokens,
            output_headroom_tokens=output_headroom_tokens,
            trigger_budget_tokens=compaction_trigger_budget_tokens,
            prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
            estimated_resume_history_tokens=estimated_resume_history_tokens,
            estimated_pre_run_tokens=estimated_pre_run_tokens,
            measured_usage_tokens=measured_usage_tokens,
            estimated_trailing_tokens=estimated_trailing_tokens,
            runs_since_latest_compaction=runs_since_compaction,
        )

    if estimated_pre_run_tokens < compaction_trigger_budget_tokens:
        return CompactionBudgetReport(
            should_compact=False,
            reason="within_budget",
            context_window_tokens=context_window_tokens,
            effective_context_window_tokens=effective_context_window_tokens,
            output_headroom_tokens=output_headroom_tokens,
            trigger_budget_tokens=compaction_trigger_budget_tokens,
            prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
            estimated_resume_history_tokens=estimated_resume_history_tokens,
            estimated_pre_run_tokens=estimated_pre_run_tokens,
            measured_usage_tokens=measured_usage_tokens,
            estimated_trailing_tokens=estimated_trailing_tokens,
            runs_since_latest_compaction=runs_since_compaction,
        )

    return CompactionBudgetReport(
        should_compact=True,
        reason="over_budget",
        context_window_tokens=context_window_tokens,
        effective_context_window_tokens=effective_context_window_tokens,
        output_headroom_tokens=output_headroom_tokens,
        trigger_budget_tokens=compaction_trigger_budget_tokens,
        prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
        estimated_resume_history_tokens=estimated_resume_history_tokens,
        estimated_pre_run_tokens=estimated_pre_run_tokens,
        measured_usage_tokens=measured_usage_tokens,
        estimated_trailing_tokens=estimated_trailing_tokens,
        runs_since_latest_compaction=runs_since_compaction,
    )


def _estimate_resume_history_budget_components(
    loaded_session: LoadedSession,
) -> tuple[int, int | None, int | None]:
    resume_history = build_resume_message_history(loaded_session)
    measured_usage = _find_last_measured_usage_tokens(resume_history)
    if measured_usage is not None:
        usage_tokens, last_usage_index = measured_usage
        trailing_messages = resume_history[last_usage_index + 1 :]
        trailing_tokens = math.ceil(
            _estimate_message_history_chars(trailing_messages)
            / COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        )
        return usage_tokens + trailing_tokens, usage_tokens, trailing_tokens

    return (
        math.ceil(
            _estimate_message_history_chars(resume_history)
            / COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        ),
        None,
        None,
    )


def _estimate_resume_history_tokens(loaded_session: LoadedSession) -> int:
    return _estimate_resume_history_budget_components(loaded_session)[0]


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


__all__ = [
    "build_auto_compact_session_budget_report",
    "should_auto_compact_session",
]
