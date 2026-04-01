from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.compaction.constants import (
    SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION,
    SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS,
)
from just_another_coding_agent.runtime.compaction.working_set import (
    merge_summary_paths,
    with_deterministic_working_set_paths,
)
from just_another_coding_agent.runtime.models import (
    get_model_context_window_tokens,
    resolve_canonical_model,
)
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    load_session,
)

from . import source_builder as source_builder_module
from . import trigger as trigger_module

COMPACTION_SUMMARY_INSTRUCTIONS = "\n".join(
    [
        "You summarize coding-agent session state into a structured compaction record.",
        "Preserve only durable information needed to continue the work correctly.",
        "Do not invent facts, files, preferences, or unresolved work.",
        "Prefer short concrete items over verbose prose.",
        "Use current_objective for the active user goal at the compaction boundary.",
        (
            "Use established_facts for confirmed outcomes, code changes, "
            "and verified behavior."
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


async def summarize_session_for_compaction(
    *,
    model: Any,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    summarizer = Agent(
        resolve_canonical_model(model),
        output_type=SessionCompactionSummary,
        instructions=COMPACTION_SUMMARY_INSTRUCTIONS,
    )
    result = await summarizer.run(_build_compaction_source(loaded_session, model=model))
    normalized = with_deterministic_working_set_paths(
        _normalize_compaction_summary(result.output),
        loaded_session=loaded_session,
    )
    if (
        normalized.current_objective is None
        and not normalized.established_facts
        and not normalized.user_preferences
        and not normalized.important_paths
        and not normalized.read_paths
        and not normalized.modified_paths
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

    summary = await summarize_session_for_compaction(
        model=model,
        loaded_session=loaded_session,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
    )


def _normalize_compaction_summary(
    summary: SessionCompactionSummary,
) -> SessionCompactionSummary:
    current_objective = _normalize_optional_text(summary.current_objective)
    return SessionCompactionSummary(
        current_objective=current_objective,
        established_facts=_normalize_summary_items(summary.established_facts),
        user_preferences=_normalize_summary_items(summary.user_preferences),
        important_paths=_normalize_summary_items(summary.important_paths),
        read_paths=_normalize_summary_items(summary.read_paths),
        modified_paths=_normalize_summary_items(summary.modified_paths),
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
    "COMPACTION_SUMMARY_INSTRUCTIONS",
    "SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION",
    "SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS",
    "should_auto_compact_session",
    "summarize_and_append_compaction_to_session",
    "summarize_session_for_compaction",
]
