from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
    SessionRunRecord,
)
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    load_session,
)

AUTO_COMPACTION_RUN_THRESHOLD = 5
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

    @summarizer.output_validator
    def validate_summary(
        summary: SessionCompactionSummary,
    ) -> SessionCompactionSummary:
        normalized = _normalize_compaction_summary(summary)
        if (
            normalized.current_objective is None
            and not normalized.established_facts
            and not normalized.user_preferences
            and not normalized.important_paths
            and not normalized.open_questions
            and not normalized.unresolved_work
        ):
            raise ModelRetry(
                "Compaction summary is empty. Preserve at least one durable "
                "objective, fact, preference, path, question, or unresolved task."
            )

        return normalized

    result = await summarizer.run(_build_compaction_source(loaded_session))
    return result.output


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


def should_auto_compact_session(loaded_session: LoadedSession) -> bool:
    if not loaded_session.runs:
        return False

    return (
        _runs_since_latest_compaction(loaded_session)
        >= AUTO_COMPACTION_RUN_THRESHOLD
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


def _build_compaction_source(loaded_session: LoadedSession) -> str:
    latest_compaction = loaded_session.latest_compaction
    start_index = 0
    sections: list[str] = []

    if latest_compaction is not None:
        sections.append("Previous compaction summary:")
        sections.append(_render_summary(latest_compaction.summary))
        start_index = _run_index_for_id(
            loaded_session,
            latest_compaction.summarized_through_run_id,
        ) + 1

    sections.append("Runs since the latest compaction boundary:")
    if start_index >= len(loaded_session.runs):
        sections.append("(no new runs)")
    else:
        for run in loaded_session.runs[start_index:]:
            sections.append(_render_run(run))

    return "\n\n".join(sections)


def _render_summary(summary: SessionCompactionSummary) -> str:
    lines: list[str] = []
    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")
    _append_rendered_section(lines, "Established facts", summary.established_facts)
    _append_rendered_section(lines, "User preferences", summary.user_preferences)
    _append_rendered_section(lines, "Important paths", summary.important_paths)
    _append_rendered_section(lines, "Open questions", summary.open_questions)
    _append_rendered_section(lines, "Unresolved work", summary.unresolved_work)
    return "\n".join(lines) if lines else "(empty summary)"


def _append_rendered_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)


def _render_run(run: SessionRunRecord) -> str:
    lines = [f"Run {run.run_id}", f"Prompt: {run.prompt}"]
    if run.thinking is not None:
        lines.append(f"Thinking: {run.thinking}")

    lines.append("Messages:")
    for message in run.messages:
        lines.extend(f"- {line}" for line in _render_message(message))

    lines.append("Events:")
    for event in run.events:
        lines.append(f"- {event.type}: {event.model_dump_json()}")

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
        open_questions=_normalize_summary_items(summary.open_questions),
        unresolved_work=_normalize_summary_items(summary.unresolved_work),
    )


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


def _render_message(message: ModelMessage) -> list[str]:
    rendered_parts: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            rendered_parts.append(f"user: {part.content}")
        elif isinstance(part, SystemPromptPart):
            rendered_parts.append(f"system: {part.content}")
        elif isinstance(part, TextPart):
            rendered_parts.append(f"assistant: {part.content}")
        elif isinstance(part, ThinkingPart):
            rendered_parts.append(f"assistant_thinking: {part.content}")
        elif isinstance(part, ToolCallPart):
            rendered_parts.append(
                f"tool_call {part.tool_name}: {part.args_as_json_str()}"
            )
        elif isinstance(part, ToolReturnPart):
            rendered_parts.append(
                f"tool_return {part.tool_name}: {part.model_response_str()}"
            )
        else:
            raise RuntimeError(
                "Unsupported message part for compaction: "
                f"{type(part).__name__}"
            )

    return rendered_parts


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")
