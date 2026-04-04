from __future__ import annotations

from typing import Any

from just_another_coding_agent.contracts.compaction import (
    COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
)
from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import LoadedSession, SessionRunRecord
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction_boundary,
)
from just_another_coding_agent.runtime.compaction.budget import (
    build_effective_compaction_context_window_tokens,
)
from just_another_coding_agent.runtime.compaction.constants import (
    DEFAULT_SESSION_COMPACTION_SOURCE_CHAR_LIMIT,
    MAX_COMPACTION_TEXT_FIELD_CHARS,
    MAX_COMPACTION_TOOL_ACTIVITY_LINES,
    SESSION_COMPACTION_CONTEXT_WINDOW_UTILIZATION,
)
from just_another_coding_agent.runtime.models import get_model_context_window_tokens
from just_another_coding_agent.session.jsonl import SessionFormatError
from just_another_coding_agent.session.replacement_history import (
    extract_compaction_summary_text,
)


def build_compaction_source(loaded_session: LoadedSession, *, model: Any) -> str:
    return _build_bounded_compaction_source(
        loaded_session,
        max_chars=_compaction_source_char_limit(model),
    )


def _compaction_source_char_limit(model: Any) -> int:
    context_window_tokens = get_model_context_window_tokens(model)
    if context_window_tokens is None:
        return DEFAULT_SESSION_COMPACTION_SOURCE_CHAR_LIMIT

    effective_context_window_tokens = build_effective_compaction_context_window_tokens(
        context_window_tokens
    )
    return int(
        effective_context_window_tokens
        * SESSION_COMPACTION_CONTEXT_WINDOW_UTILIZATION
        * COMPACTION_CHARS_PER_TOKEN_HEURISTIC
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
        previous_summary = extract_compaction_summary_text(
            latest_compaction.replacement_messages
        )
        if previous_summary is not None:
            sections.append("Previous compaction summary:")
            sections.append(previous_summary)

    run_sections = [
        _render_run(run)
        for run in runs_since_latest_compaction_boundary(loaded_session)
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


def _render_run(run: SessionRunRecord) -> str:
    lines = [f"Run {run.run_id}", f"Prompt: {compact_text(run.prompt)}"]
    if run.thinking is not None:
        lines.append(f"Thinking: {run.thinking}")

    terminal_lines = _render_terminal_run_outcome(run)
    tool_lines = _render_tool_activity_lines(run)
    if terminal_lines:
        lines.extend(terminal_lines)
    if tool_lines:
        lines.append("Tool outcomes:")
        lines.extend(f"- {line}" for line in tool_lines)

    return "\n".join(lines)


def _render_terminal_run_outcome(run: SessionRunRecord) -> list[str]:
    lines: list[str] = []
    for event in reversed(run.events):
        if isinstance(event, RunSucceededEvent):
            lines.append("Outcome: succeeded")
            if event.output_text:
                lines.append(f"Assistant result: {compact_text(event.output_text)}")
            return lines
        if isinstance(event, RunFailedEvent):
            lines.append(f"Outcome: failed ({event.error_type})")
            lines.append(f"Failure: {compact_text(event.message)}")
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
            rendered.append(compact_text(f"{title}: failed - {event.message}"))

    if len(rendered) > MAX_COMPACTION_TOOL_ACTIVITY_LINES:
        rendered = rendered[-MAX_COMPACTION_TOOL_ACTIVITY_LINES:]
    return rendered


def _format_tool_activity_line(title: str, summary: str | None) -> str | None:
    compact_title = compact_text(title)
    if summary is None:
        return compact_title or None

    compact_summary = compact_text(summary)
    if not compact_summary:
        return compact_title or None
    if compact_summary == compact_title:
        return compact_title or None
    return f"{compact_title}: {compact_summary}"


def compact_text(
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


__all__ = ["build_compaction_source", "compact_text"]
