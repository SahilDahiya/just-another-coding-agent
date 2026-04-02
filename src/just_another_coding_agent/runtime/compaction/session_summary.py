from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction_boundary,
)
from just_another_coding_agent.runtime.compaction.working_set import (
    merge_summary_paths,
    with_deterministic_survival_state,
)
from just_another_coding_agent.runtime.models import (
    get_model_context_window_tokens,
    resolve_canonical_model,
)
from just_another_coding_agent.session.checkpoint import (
    select_compaction_checkpoint_tail,
)
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    load_session,
)

from . import source_builder as source_builder_module
from . import trigger as trigger_module
from .constants import SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS

COMPACTION_SUMMARY_INSTRUCTIONS = "\n".join(
    [
        "You summarize coding-agent session state into a structured compaction record.",
        "Preserve only durable information needed to continue the work correctly.",
        "Do not invent facts, files, preferences, or unresolved work.",
        "Prefer short concrete items over verbose prose.",
        "Use current_objective for the active user goal at the compaction boundary.",
        "Use current_plan for the active plan that should still guide continuation.",
        (
            "Use established_facts for confirmed outcomes, code changes, "
            "and verified behavior."
        ),
        (
            "Use completed_work for important finished milestones that should "
            "not be redone."
        ),
        (
            "Use key_decisions for durable implementation or workflow "
            "decisions already made."
        ),
        "Use user_preferences only for stable user instructions or preferences.",
        (
            "Use important_paths for files or directories that matter to "
            "continuing the work."
        ),
        "Use open_questions for unresolved unknowns or clarification gaps.",
        "Use unresolved_work for concrete next actions that still need to happen.",
        "Return empty lists when a section has nothing durable to keep.",
    ]
)


class _NarrativeCompactionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_objective: str | None = None
    current_plan: list[str] = Field(default_factory=list)
    established_facts: list[str] = Field(default_factory=list)
    completed_work: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    user_preferences: list[str] = Field(default_factory=list)
    important_paths: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unresolved_work: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _AutoCompactionTarget:
    summary_session: LoadedSession
    summarized_through_run_id: str
    first_kept_run_id: str | None
    checkpoint_messages: list[ModelMessage] | None = None


async def summarize_session_for_compaction(
    *,
    model: Any,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    summarizer = Agent(
        resolve_canonical_model(model),
        output_type=_NarrativeCompactionSummary,
        instructions=COMPACTION_SUMMARY_INSTRUCTIONS,
    )
    result = await summarizer.run(_build_compaction_source(loaded_session, model=model))
    normalized = with_deterministic_survival_state(
        _normalize_compaction_summary(result.output),
        loaded_session=loaded_session,
    )
    if (
        normalized.current_objective is None
        and not normalized.current_plan
        and not normalized.established_facts
        and not normalized.completed_work
        and not normalized.key_decisions
        and not normalized.user_preferences
        and not normalized.important_paths
        and not normalized.read_paths
        and not normalized.modified_paths
        and not normalized.recent_shell_commands
        and not normalized.recent_verifications
        and not normalized.recent_failures
        and not normalized.open_questions
        and not normalized.unresolved_work
    ):
        raise SessionFormatError(
            "Compaction summary is empty. Preserve at least one durable "
            "objective, fact, preference, path, question, or unresolved task."
        )
    return normalized


async def summarize_and_append_compaction_to_session(
    *,
    model: Any,
    path,
    workspace_root,
) -> SessionCompactionEntry:
    loaded_session = load_session(
        path=path,
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
    )
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    target = _build_auto_compaction_target(loaded_session)

    summary = await summarize_session_for_compaction(
        model=model,
        loaded_session=target.summary_session,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
        summarized_through_run_id=target.summarized_through_run_id,
        first_kept_run_id=target.first_kept_run_id,
        checkpoint_messages=target.checkpoint_messages,
    )


def _normalize_compaction_summary(
    summary: _NarrativeCompactionSummary,
) -> SessionCompactionSummary:
    current_objective = _normalize_optional_text(summary.current_objective)
    return SessionCompactionSummary(
        current_objective=current_objective,
        current_plan=_normalize_summary_items(summary.current_plan),
        established_facts=_normalize_summary_items(summary.established_facts),
        completed_work=_normalize_summary_items(summary.completed_work),
        key_decisions=_normalize_summary_items(summary.key_decisions),
        user_preferences=_normalize_summary_items(summary.user_preferences),
        important_paths=_normalize_summary_items(summary.important_paths),
        open_questions=_normalize_summary_items(summary.open_questions),
        unresolved_work=_normalize_summary_items(summary.unresolved_work),
    )


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_summary_items(values: list[str]) -> list[str]:
    return merge_summary_paths(values)


def _build_auto_compaction_target(
    loaded_session: LoadedSession,
) -> _AutoCompactionTarget:
    retained_runs = runs_since_latest_compaction_boundary(loaded_session)
    retained_messages, first_kept_run_id, split_within_run = (
        select_compaction_checkpoint_tail(
            retained_runs,
            token_budget=SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS,
        )
    )
    if first_kept_run_id is None:
        return _AutoCompactionTarget(
            summary_session=loaded_session,
            summarized_through_run_id=loaded_session.runs[-1].run_id,
            first_kept_run_id=None,
            checkpoint_messages=None,
        )

    kept_run_index = next(
        index
        for index, run in enumerate(loaded_session.runs)
        if run.run_id == first_kept_run_id
    )
    summarized_run_index = kept_run_index if split_within_run else kept_run_index - 1
    if summarized_run_index < 0:
        return _AutoCompactionTarget(
            summary_session=loaded_session,
            summarized_through_run_id=loaded_session.runs[-1].run_id,
            first_kept_run_id=None,
            checkpoint_messages=None,
        )

    summary_session = LoadedSession(
        header=loaded_session.header,
        fork=loaded_session.fork,
        name=loaded_session.name,
        runs=list(loaded_session.runs[: summarized_run_index + 1]),
        compactions=list(loaded_session.compactions),
    )
    return _AutoCompactionTarget(
        summary_session=summary_session,
        summarized_through_run_id=summary_session.runs[-1].run_id,
        first_kept_run_id=first_kept_run_id,
        checkpoint_messages=retained_messages,
    )


def should_auto_compact_session(
    loaded_session: LoadedSession,
    *,
    model: Any,
) -> bool:
    return trigger_module.should_auto_compact_session(
        loaded_session,
        model=model,
        get_context_window_tokens=get_model_context_window_tokens,
    )


def build_auto_compact_session_budget_report(
    loaded_session: LoadedSession,
    *,
    model: Any,
):
    return trigger_module.build_auto_compact_session_budget_report(
        loaded_session,
        model=model,
        get_context_window_tokens=get_model_context_window_tokens,
    )


def _build_compaction_source(loaded_session: LoadedSession, *, model: Any) -> str:
    return source_builder_module.build_compaction_source(loaded_session, model=model)


def _build_bounded_compaction_source(
    loaded_session: LoadedSession,
    *,
    max_chars: int,
) -> str:
    return source_builder_module._build_bounded_compaction_source(
        loaded_session,
        max_chars=max_chars,
    )


__all__ = [
    "build_auto_compact_session_budget_report",
    "COMPACTION_SUMMARY_INSTRUCTIONS",
    "should_auto_compact_session",
    "summarize_and_append_compaction_to_session",
    "summarize_session_for_compaction",
]
