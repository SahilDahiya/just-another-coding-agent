from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

COMPACTION_CHARS_PER_TOKEN_HEURISTIC = 4

type CompactionBudgetReason = Literal[
    "no_runs",
    "unknown_context_window",
    "within_budget",
    "no_new_work",
    "over_budget",
]


class CompactionBudgetReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_compact: bool
    reason: CompactionBudgetReason
    context_window_tokens: int | None = None
    effective_context_window_tokens: int | None = None
    output_headroom_tokens: int | None = None
    trigger_budget_tokens: int | None = None
    prompt_reserve_tokens: int
    estimation_method: str
    estimated_resume_message_tokens: int
    estimated_replacement_messages_tokens: int = 0
    estimated_replacement_summary_tokens: int = 0
    estimated_pre_run_tokens: int
    estimated_post_compaction_headroom_tokens: int | None = None
    runs_since_latest_compaction: int = 0

__all__ = [
    "COMPACTION_CHARS_PER_TOKEN_HEURISTIC",
    "CompactionBudgetReason",
    "CompactionBudgetReport",
]
