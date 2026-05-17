from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.compaction import CompactionBudgetReport
from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.contracts.thinking import ThinkingSetting
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
    build_runtime_framed_resume_message_history,
)
from just_another_coding_agent.runtime.compaction.token_counting import (
    count_message_tokens,
    count_text_tokens,
)
from just_another_coding_agent.runtime.models import get_model_context_window_tokens
from just_another_coding_agent.runtime.token_estimation import estimate_messages_tokens
from just_another_coding_agent.runtime.turn_context import (
    evaluate_turn_context_baseline,
)
from just_another_coding_agent.session.replacement_history import (
    extract_compaction_summary_text,
)


@dataclass(frozen=True)
class _ResumeHistoryBudgetEstimate:
    estimation_method: str
    estimated_runtime_context_tokens: int
    estimated_resume_message_tokens: int
    estimated_replacement_messages_tokens: int
    estimated_replacement_summary_tokens: int


@dataclass(frozen=True)
class LastResponseUsageSnapshot:
    input_tokens: int
    output_tokens: int
    messages_prefix_count: int


STATIC_PROVIDER_OVERHEAD_TOKENS = 2_000
_PENDING_PROMPT_FRAMING_TOKENS = 4


def should_auto_compact_session(
    loaded_session: LoadedSession,
    *,
    model: Any,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    thinking: ThinkingSetting | None = None,
    get_context_window_tokens: Callable[[Any], int | None] | None = None,
) -> bool:
    if get_context_window_tokens is None:
        get_context_window_tokens = get_model_context_window_tokens
    return build_auto_compact_session_budget_report(
        loaded_session,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=thinking,
        get_context_window_tokens=get_context_window_tokens,
    ).should_compact


def build_auto_compact_session_budget_report(
    loaded_session: LoadedSession,
    *,
    model: Any,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    thinking: ThinkingSetting | None = None,
    get_context_window_tokens: Callable[[Any], int | None] | None = None,
) -> CompactionBudgetReport:
    if get_context_window_tokens is None:
        get_context_window_tokens = get_model_context_window_tokens
    runs_since_compaction = runs_since_latest_compaction(loaded_session)
    budget_estimate = _estimate_resume_history_budget_components(
        loaded_session,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=thinking,
    )
    estimated_pre_run_tokens = (
        budget_estimate.estimated_runtime_context_tokens
        + budget_estimate.estimated_resume_message_tokens
        + SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS
    )

    report_kwargs = dict(
        prompt_reserve_tokens=SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
        estimation_method=budget_estimate.estimation_method,
        estimated_runtime_context_tokens=(
            budget_estimate.estimated_runtime_context_tokens
        ),
        estimated_resume_message_tokens=(
            budget_estimate.estimated_resume_message_tokens
        ),
        estimated_replacement_messages_tokens=(
            budget_estimate.estimated_replacement_messages_tokens
        ),
        estimated_replacement_summary_tokens=(
            budget_estimate.estimated_replacement_summary_tokens
        ),
        estimated_pre_run_tokens=estimated_pre_run_tokens,
        runs_since_latest_compaction=runs_since_compaction,
    )

    if not loaded_session.runs:
        return CompactionBudgetReport(
            should_compact=False,
            reason="no_runs",
            **report_kwargs,
        )

    context_window_tokens = get_context_window_tokens(model)
    if context_window_tokens is None:
        return CompactionBudgetReport(
            should_compact=False,
            reason="unknown_context_window",
            **report_kwargs,
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
    estimated_post_compaction_headroom_tokens = (
        effective_context_window_tokens - estimated_pre_run_tokens
    )

    if runs_since_compaction == 0:
        return CompactionBudgetReport(
            should_compact=False,
            reason="no_new_work",
            context_window_tokens=context_window_tokens,
            effective_context_window_tokens=effective_context_window_tokens,
            output_headroom_tokens=output_headroom_tokens,
            trigger_budget_tokens=compaction_trigger_budget_tokens,
            estimated_post_compaction_headroom_tokens=(
                estimated_post_compaction_headroom_tokens
            ),
            **report_kwargs,
        )

    if estimated_pre_run_tokens < compaction_trigger_budget_tokens:
        return CompactionBudgetReport(
            should_compact=False,
            reason="within_budget",
            context_window_tokens=context_window_tokens,
            effective_context_window_tokens=effective_context_window_tokens,
            output_headroom_tokens=output_headroom_tokens,
            trigger_budget_tokens=compaction_trigger_budget_tokens,
            estimated_post_compaction_headroom_tokens=(
                estimated_post_compaction_headroom_tokens
            ),
            **report_kwargs,
        )

    return CompactionBudgetReport(
        should_compact=True,
        reason="over_budget",
        context_window_tokens=context_window_tokens,
        effective_context_window_tokens=effective_context_window_tokens,
        output_headroom_tokens=output_headroom_tokens,
        trigger_budget_tokens=compaction_trigger_budget_tokens,
        estimated_post_compaction_headroom_tokens=(
            estimated_post_compaction_headroom_tokens
        ),
        **report_kwargs,
    )


def _estimate_resume_history_budget_components(
    loaded_session: LoadedSession,
    *,
    model: Any,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    thinking: ThinkingSetting | None = None,
) -> _ResumeHistoryBudgetEstimate:
    resolved_workspace_root = (
        loaded_session.header.workspace_root
        if workspace_root is None
        else workspace_root
    )
    resolved_shell_family = (
        loaded_session.header.shell_family
        if shell_family is None
        else shell_family
    )
    baseline_decision = evaluate_turn_context_baseline(
        entry=loaded_session.latest_turn_context,
        model=model,
        workspace_root=resolved_workspace_root,
        current_date=current_date,
        shell_family=resolved_shell_family,
        thinking=thinking,
        mcp_inventory=getattr(loaded_session, "latest_mcp_inventory", None),
        has_persisted_history=loaded_session.has_persisted_turn_context_history,
    )
    runtime_context_estimate = estimate_messages_tokens(
        model=model,
        messages=build_runtime_framed_resume_message_history(
            None,
            baseline_decision=baseline_decision,
            model=model,
            workspace_root=resolved_workspace_root,
            current_date=current_date,
            shell_family=resolved_shell_family,
            thinking=thinking,
            mcp_inventory=getattr(loaded_session, "latest_mcp_inventory", None),
        ),
    )
    resume_history_estimate = estimate_messages_tokens(
        model=model,
        messages=build_resume_message_history(loaded_session),
    )
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return _ResumeHistoryBudgetEstimate(
            estimation_method=resume_history_estimate.estimation_method,
            estimated_runtime_context_tokens=(
                runtime_context_estimate.estimated_tokens
            ),
            estimated_resume_message_tokens=resume_history_estimate.estimated_tokens,
            estimated_replacement_messages_tokens=0,
            estimated_replacement_summary_tokens=0,
        )

    replacement_messages_estimate = estimate_messages_tokens(
        model=model,
        messages=latest_compaction.replacement_messages,
    )
    summary_text = extract_compaction_summary_text(
        latest_compaction.replacement_messages
    )
    if summary_text is None:
        estimated_replacement_summary_tokens = 0
    else:
        estimated_replacement_summary_tokens = estimate_messages_tokens(
            model=model,
            messages=latest_compaction.replacement_messages[-1:],
        ).estimated_tokens

    return _ResumeHistoryBudgetEstimate(
        estimation_method=resume_history_estimate.estimation_method,
        estimated_runtime_context_tokens=runtime_context_estimate.estimated_tokens,
        estimated_resume_message_tokens=resume_history_estimate.estimated_tokens,
        estimated_replacement_messages_tokens=(
            replacement_messages_estimate.estimated_tokens
        ),
        estimated_replacement_summary_tokens=estimated_replacement_summary_tokens,
    )


def estimate_next_request_input_tokens(
    messages: Sequence[ModelMessage],
    *,
    model: Any,
    last_response_usage: LastResponseUsageSnapshot | None = None,
    pending_prompt: str | None = None,
) -> int:
    if last_response_usage is None:
        base = (
            count_message_tokens(messages, model=model)
            + STATIC_PROVIDER_OVERHEAD_TOKENS
        )
    else:
        delta_messages = messages[last_response_usage.messages_prefix_count :]
        delta_tokens = count_message_tokens(delta_messages, model=model)
        base = (
            last_response_usage.input_tokens
            + last_response_usage.output_tokens
            + delta_tokens
        )

    if pending_prompt:
        base += (
            count_text_tokens(model=model, text=pending_prompt)
            + _PENDING_PROMPT_FRAMING_TOKENS
        )

    return base


def check_in_run_compaction_needed(
    messages: Sequence[ModelMessage],
    *,
    model: Any,
    last_response_usage: LastResponseUsageSnapshot | None = None,
    pending_prompt: str | None = None,
    get_context_window_tokens: Callable[[Any], int | None] = (
        get_model_context_window_tokens
    ),
) -> bool:
    context_window_tokens = get_context_window_tokens(model)
    if context_window_tokens is None:
        return False

    effective_context_window_tokens = build_effective_compaction_context_window_tokens(
        context_window_tokens
    )
    trigger_budget_tokens = math.floor(
        (
            effective_context_window_tokens
            * SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION
        )
        + 1e-6
    )

    estimated_tokens = estimate_next_request_input_tokens(
        messages,
        model=model,
        last_response_usage=last_response_usage,
        pending_prompt=pending_prompt,
    )

    return estimated_tokens >= trigger_budget_tokens


__all__ = [
    "LastResponseUsageSnapshot",
    "STATIC_PROVIDER_OVERHEAD_TOKENS",
    "build_auto_compact_session_budget_report",
    "check_in_run_compaction_needed",
    "estimate_next_request_input_tokens",
    "should_auto_compact_session",
]
