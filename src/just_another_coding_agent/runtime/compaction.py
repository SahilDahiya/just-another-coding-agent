from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
)
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
)

COMPACTION_SUMMARY_DYNAMIC_REF = "session-compaction-summary"
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


def build_session_history_processor(
    loaded_session: LoadedSession,
) -> Callable[[list[ModelMessage]], list[ModelMessage]] | None:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return None

    summary_run_index = _run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    retained_messages = [
        message
        for run in loaded_session.runs[summary_run_index + 1 :]
        for message in run.messages
    ]
    compacted_prefix = [
        build_compaction_summary_message(latest_compaction.summary),
        *retained_messages,
    ]

    def apply_compaction(messages: list[ModelMessage]) -> list[ModelMessage]:
        if messages and _starts_with_compaction_summary(messages):
            return messages

        # PydanticAI passes resumed history in front of the live run messages.
        # Those persisted messages arrive without a run_id, while messages
        # created during the in-flight run are tagged with the current run_id.
        # This boundary is the seam we rely on to replace the summarized prefix.
        persisted_prefix_length = _leading_persisted_message_count(messages)
        if persisted_prefix_length == 0:
            raise RuntimeError(
                "Compaction history processor could not match the expected "
                "persisted history prefix"
            )

        return compacted_prefix + messages[persisted_prefix_length:]

    return apply_compaction


async def summarize_session_for_compaction(
    *,
    model: Any,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    if not loaded_session.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    summarizer = Agent(
        model,
        output_type=SessionCompactionSummary,
        instructions=COMPACTION_SUMMARY_INSTRUCTIONS,
    )
    result = await summarizer.run(_build_compaction_source(loaded_session))
    return result.output


async def summarize_and_append_compaction_to_session(
    *,
    model: Any,
    path,
    workspace_root,
) -> SessionCompactionEntry:
    from just_another_coding_agent.session.jsonl import load_session

    loaded_session = load_session(path=path, workspace_root=workspace_root)
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


def strip_compaction_summary_from_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    sanitized: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            sanitized.append(message)
            continue

        kept_parts = [
            part for part in message.parts if not _is_compaction_summary_part(part)
        ]
        if not kept_parts:
            continue

        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue

        sanitized.append(message.model_copy(update={"parts": kept_parts}))

    return sanitized


def should_auto_compact_session(loaded_session: LoadedSession) -> bool:
    if not loaded_session.runs:
        return False

    return (
        _runs_since_latest_compaction(loaded_session)
        >= AUTO_COMPACTION_RUN_THRESHOLD
    )


def build_compaction_summary_message(
    summary: SessionCompactionSummary,
) -> ModelRequest:
    lines = ["Session compaction summary:"]

    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")

    _append_summary_section(lines, "Established facts", summary.established_facts)
    _append_summary_section(lines, "User preferences", summary.user_preferences)
    _append_summary_section(lines, "Important paths", summary.important_paths)
    _append_summary_section(lines, "Open questions", summary.open_questions)
    _append_summary_section(lines, "Unresolved work", summary.unresolved_work)

    return ModelRequest(
        parts=[
            SystemPromptPart(
                content="\n".join(lines),
                dynamic_ref=COMPACTION_SUMMARY_DYNAMIC_REF,
            )
        ]
    )


def _append_summary_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)


def _is_compaction_summary_part(part: ModelRequestPart) -> bool:
    return (
        isinstance(part, SystemPromptPart)
        and part.dynamic_ref == COMPACTION_SUMMARY_DYNAMIC_REF
    )


def _starts_with_compaction_summary(messages: list[ModelMessage]) -> bool:
    if not messages:
        return False

    first_message = messages[0]
    if not isinstance(first_message, ModelRequest):
        return False

    return any(_is_compaction_summary_part(part) for part in first_message.parts)


def _leading_persisted_message_count(messages: list[ModelMessage]) -> int:
    count = 0
    for message in messages:
        # See apply_compaction above for the run_id boundary assumption.
        if message.run_id is not None:
            break
        count += 1
    return count


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")


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


def _render_run(run) -> str:
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


__all__ = [
    "AUTO_COMPACTION_RUN_THRESHOLD",
    "COMPACTION_SUMMARY_DYNAMIC_REF",
    "COMPACTION_SUMMARY_INSTRUCTIONS",
    "build_compaction_summary_message",
    "build_session_history_processor",
    "summarize_and_append_compaction_to_session",
    "summarize_session_for_compaction",
    "should_auto_compact_session",
    "strip_compaction_summary_from_messages",
]
