from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.models import (
    build_canonical_model_settings,
    get_model_context_window_tokens,
    resolve_canonical_model,
)
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    load_session,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_replacement_messages,
)

from . import source_builder as source_builder_module
from . import trigger as trigger_module
from .constants import SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS
from .resume import build_resume_message_history

COMPACTION_SUMMARY_INSTRUCTIONS = "\n".join(
    [
        "You summarize coding-agent session state into one compact continuation note.",
        "Preserve only durable information needed to continue the work correctly.",
        "Do not invent facts, files, preferences, or unresolved work.",
        "Write only short bullet lines under supported section headings.",
        "Use these section headings only when supported by evidence:",
        "Primary Intent:",
        "Completed Work:",
        "Important Files/Paths:",
        "Failures / Open Issues:",
        "Current State:",
        "Next Step:",
        "Stable Preferences:",
        "List files or paths only when they are explicitly visible in prompts, "
        "assistant results, or tool evidence.",
        "Do not include code snippets, function signatures, raw transcript "
        "dumps, or exhaustive user-message lists.",
        "Do not repeat the same fact across multiple sections.",
        "Omit any section that has no concrete evidence.",
        "Watch for bloat and rot: aggressively omit stale, repetitive, "
        "low-signal, or speculative detail.",
        "Keep the whole note concise: prefer a few strong bullets over "
        "exhaustive detail.",
        "Skip transient noise, repetitive chatter, and low-signal tool details.",
    ]
)


async def summarize_session_for_compaction(
    *,
    model: Any,
    loaded_session: LoadedSession,
) -> str:
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    resolved_model = resolve_canonical_model(model)
    summarizer = Agent(
        resolved_model,
        output_type=str,
        instructions=COMPACTION_SUMMARY_INSTRUCTIONS,
    )
    async with summarizer.run_stream(
        _build_compaction_source(loaded_session, model=model),
        model_settings=build_canonical_model_settings(model=resolved_model),
    ) as result:
        summary_text = await result.get_output()
    normalized = _normalize_compaction_summary_text(summary_text)
    if not normalized:
        raise SessionFormatError(
            "Compaction summary is empty. Preserve at least one durable "
            "objective, fact, path, question, or unresolved task."
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

    summary_text = await summarize_session_for_compaction(
        model=model,
        loaded_session=loaded_session,
    )
    replacement_messages = build_compaction_replacement_messages(
        model=model,
        messages=build_resume_message_history(loaded_session),
        summary_text=summary_text,
        token_budget=SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=replacement_messages,
    )


def _normalize_compaction_summary_text(summary_text: str) -> str:
    lines = [line.strip() for line in summary_text.splitlines()]
    kept_lines = [line for line in lines if line]
    return "\n".join(kept_lines)


def should_auto_compact_session(
    loaded_session: LoadedSession,
    *,
    model: Any,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    thinking: ThinkingSetting | None = None,
) -> bool:
    return trigger_module.should_auto_compact_session(
        loaded_session,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=thinking,
        get_context_window_tokens=get_model_context_window_tokens,
    )


def build_auto_compact_session_budget_report(
    loaded_session: LoadedSession,
    *,
    model: Any,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    thinking: ThinkingSetting | None = None,
):
    return trigger_module.build_auto_compact_session_budget_report(
        loaded_session,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=thinking,
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
