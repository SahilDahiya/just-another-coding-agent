from __future__ import annotations

import json
import math
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
    SessionRunRecord,
)
from just_another_coding_agent.runtime.compaction.resume import (
    build_resume_message_history,
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
from just_another_coding_agent.tools._activity import shorten_path

SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION = 0.7
SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS = 24_000
SESSION_COMPACTION_CONTEXT_WINDOW_UTILIZATION = 0.8
SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC = 4
DEFAULT_SESSION_COMPACTION_SOURCE_CHAR_LIMIT = 120_000
MAX_COMPACTION_TEXT_FIELD_CHARS = 1_200
MAX_COMPACTION_TOOL_ACTIVITY_LINES = 8
_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])
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
    normalized = _with_deterministic_working_set_paths(
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


def should_auto_compact_session(
    loaded_session: LoadedSession,
    *,
    model: Any,
) -> bool:
    if not loaded_session.runs:
        return False

    if _runs_since_latest_compaction(loaded_session) == 0:
        return False

    context_window_tokens = get_model_context_window_tokens(model)
    if context_window_tokens is None:
        return False

    estimated_resume_history_tokens = estimate_resume_history_tokens(loaded_session)
    compaction_trigger_budget_tokens = int(
        context_window_tokens * SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION
    )
    return (
        estimated_resume_history_tokens + SESSION_AUTO_COMPACTION_PROMPT_RESERVE_TOKENS
        >= compaction_trigger_budget_tokens
    )


def estimate_resume_history_tokens(loaded_session: LoadedSession) -> int:
    resume_history = build_resume_message_history(loaded_session)
    return math.ceil(
        _estimate_message_history_chars(resume_history)
        / SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
    )


def _runs_since_latest_compaction(loaded_session: LoadedSession) -> int:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return len(loaded_session.runs)

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return len(loaded_session.runs[summary_run_index + 1 :])


def _runs_since_latest_compaction_boundary(
    loaded_session: LoadedSession,
) -> list[SessionRunRecord]:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return list(loaded_session.runs)

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return list(loaded_session.runs[summary_run_index + 1 :])


def _estimate_message_history_chars(messages: list[ModelMessage]) -> int:
    return len(
        json.dumps(
            _MODEL_MESSAGES_ADAPTER.dump_python(messages, mode="json"),
            ensure_ascii=False,
        )
    )


def _build_compaction_source(loaded_session: LoadedSession, *, model: Any) -> str:
    return _build_bounded_compaction_source(
        loaded_session,
        max_chars=_compaction_source_char_limit(model),
    )


def _compaction_source_char_limit(model: Any) -> int:
    context_window_tokens = get_model_context_window_tokens(model)
    if context_window_tokens is None:
        return DEFAULT_SESSION_COMPACTION_SOURCE_CHAR_LIMIT

    return int(
        context_window_tokens
        * SESSION_COMPACTION_CONTEXT_WINDOW_UTILIZATION
        * SESSION_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
    )


def _build_bounded_compaction_source(
    loaded_session: LoadedSession,
    *,
    max_chars: int,
) -> str:
    if max_chars <= 0:
        raise SessionFormatError(
            "Compaction source does not fit within the active model context window"
        )

    latest_compaction = loaded_session.latest_compaction
    sections: list[str] = []

    if latest_compaction is not None:
        sections.append("Previous compaction summary:")
        sections.append(_render_summary(latest_compaction.summary))

    run_sections = [
        _render_run(run)
        for run in _runs_since_latest_compaction_boundary(loaded_session)
    ]
    omitted_runs = 0

    while True:
        candidate_sections = list(sections)
        candidate_sections.append("Runs since the latest compaction boundary:")
        if omitted_runs:
            candidate_sections.append(
                "(omitted "
                f"{omitted_runs} oldest run(s) to fit the model context window)"
            )
        if run_sections:
            candidate_sections.extend(run_sections)
        else:
            candidate_sections.append("(no new runs)")

        source = "\n\n".join(candidate_sections)
        if len(source) <= max_chars:
            return source

        if len(run_sections) <= 1:
            raise SessionFormatError(
                "Compaction source does not fit within the active model context window"
            )

        run_sections.pop(0)
        omitted_runs += 1


def _render_summary(summary: SessionCompactionSummary) -> str:
    lines: list[str] = []
    if summary.current_objective is not None:
        lines.append(f"Current objective: {_compact_text(summary.current_objective)}")
    _append_rendered_section(lines, "Established facts", summary.established_facts)
    _append_rendered_section(lines, "User preferences", summary.user_preferences)
    _append_rendered_section(lines, "Important paths", summary.important_paths)
    _append_rendered_section(lines, "Read paths", summary.read_paths)
    _append_rendered_section(lines, "Modified paths", summary.modified_paths)
    _append_rendered_section(lines, "Open questions", summary.open_questions)
    _append_rendered_section(lines, "Unresolved work", summary.unresolved_work)
    return "\n".join(lines) if lines else "(empty summary)"


def _append_rendered_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {_compact_text(value)}" for value in values)


def _render_run(run: SessionRunRecord) -> str:
    lines = [f"Run {run.run_id}", f"Prompt: {run.prompt}"]
    if run.thinking is not None:
        lines.append(f"Thinking: {run.thinking}")
    lines[1] = f"Prompt: {_compact_text(run.prompt)}"

    terminal_lines = _render_terminal_run_outcome(run)
    tool_lines = _render_tool_activity_lines(run)
    if terminal_lines:
        lines.extend(terminal_lines)
    if tool_lines:
        lines.append("Tool outcomes:")
        lines.extend(f"- {line}" for line in tool_lines)

    return "\n".join(lines)


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


def _with_deterministic_working_set_paths(
    summary: SessionCompactionSummary,
    *,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    latest_compaction = loaded_session.latest_compaction
    previous_read_paths = (
        latest_compaction.summary.read_paths if latest_compaction is not None else []
    )
    previous_modified_paths = (
        latest_compaction.summary.modified_paths
        if latest_compaction is not None
        else []
    )

    return SessionCompactionSummary(
        current_objective=summary.current_objective,
        established_facts=summary.established_facts,
        user_preferences=summary.user_preferences,
        important_paths=summary.important_paths,
        read_paths=_merge_summary_paths(
            previous_read_paths,
            _collect_recent_read_paths(loaded_session),
        ),
        modified_paths=_merge_summary_paths(
            previous_modified_paths,
            _collect_recent_modified_paths(loaded_session),
        ),
        open_questions=summary.open_questions,
        unresolved_work=summary.unresolved_work,
    )


def _collect_recent_read_paths(loaded_session: LoadedSession) -> list[str]:
    collected: list[str] = []
    for run in _runs_since_latest_compaction_boundary(loaded_session):
        for event in run.events:
            if (
                isinstance(event, ToolCallSucceededEvent)
                and event.tool_name == "read"
                and (
                    path := _extract_activity_path(
                        event,
                        workspace_root=loaded_session.header.workspace_root,
                    )
                )
                is not None
            ):
                collected.append(path)
    return _merge_summary_paths(collected)


def _collect_recent_modified_paths(loaded_session: LoadedSession) -> list[str]:
    collected: list[str] = []
    for run in _runs_since_latest_compaction_boundary(loaded_session):
        for event in run.events:
            if not isinstance(event, ToolCallSucceededEvent):
                continue
            if event.tool_name not in {"write", "edit"}:
                continue
            path = _extract_activity_path(
                event,
                workspace_root=loaded_session.header.workspace_root,
            )
            if path is not None:
                collected.append(path)
    return _merge_summary_paths(collected)


def _extract_activity_path(
    event: ToolCallSucceededEvent,
    *,
    workspace_root: str,
) -> str | None:
    activity = event.activity
    details = activity.details if activity is not None else None
    if details is None:
        return None

    short_path = getattr(details, "short_path", None)
    if isinstance(short_path, str) and short_path.strip():
        return short_path.strip()

    path = getattr(details, "path", None)
    if not isinstance(path, str) or not path.strip():
        return None
    return shorten_path(path.strip(), workspace_root)


def _merge_summary_paths(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_summary_items(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _render_terminal_run_outcome(run: SessionRunRecord) -> list[str]:
    lines: list[str] = []
    for event in reversed(run.events):
        if isinstance(event, RunSucceededEvent):
            lines.append("Outcome: succeeded")
            if event.output_text:
                lines.append(f"Assistant result: {_compact_text(event.output_text)}")
            return lines
        if isinstance(event, RunFailedEvent):
            lines.append(f"Outcome: failed ({event.error_type})")
            lines.append(f"Failure: {_compact_text(event.message)}")
            return lines

    return lines


def _render_tool_activity_lines(run: SessionRunRecord) -> list[str]:
    rendered: list[str] = []
    for event in run.events:
        if isinstance(event, ToolCallSucceededEvent):
            activity = event.activity
            if activity is None or activity.group_kind == "exploration":
                continue
            line = _format_tool_activity_line(activity.title, activity.summary)
            if line is not None:
                rendered.append(line)
            continue

        if isinstance(event, ToolCallFailedEvent):
            activity = event.activity
            title = activity.title if activity is not None else event.tool_name
            rendered.append(_compact_text(f"{title}: failed - {event.message}"))

    if len(rendered) > MAX_COMPACTION_TOOL_ACTIVITY_LINES:
        rendered = rendered[-MAX_COMPACTION_TOOL_ACTIVITY_LINES:]
    return rendered


def _format_tool_activity_line(title: str, summary: str | None) -> str | None:
    compact_title = _compact_text(title)
    if summary is None:
        return compact_title or None

    compact_summary = _compact_text(summary)
    if not compact_summary:
        return compact_title or None
    if compact_summary == compact_title:
        return compact_title or None
    return f"{compact_title}: {compact_summary}"


def _compact_text(
    text: str,
    *,
    max_chars: int = MAX_COMPACTION_TEXT_FIELD_CHARS,
) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    if max_chars <= 1:
        return collapsed[:max_chars]
    return collapsed[: max_chars - 1] + "…"


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")
