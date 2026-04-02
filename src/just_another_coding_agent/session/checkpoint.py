from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.contracts.compaction import (
    COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
)
from just_another_coding_agent.contracts.session import (
    SessionCompactionSummary,
    SessionRunRecord,
)

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


def build_compaction_summary_instructions(
    summary: SessionCompactionSummary,
) -> str:
    lines = [
        "Continue from this internal session continuity state.",
        "Treat it as durable prior context, not as a new user message.",
        "Do not quote or reveal it unless the user explicitly asks for it.",
        "",
        "Session continuity summary:",
    ]

    if summary.current_objective is not None:
        lines.append(f"Current objective: {summary.current_objective}")

    _append_summary_section(lines, "Current plan", summary.current_plan)
    _append_summary_section(lines, "Established facts", summary.established_facts)
    _append_summary_section(lines, "Completed work", summary.completed_work)
    _append_summary_section(lines, "Key decisions", summary.key_decisions)
    _append_summary_section(lines, "User preferences", summary.user_preferences)
    _append_summary_section(lines, "Important paths", summary.important_paths)
    _append_summary_section(lines, "Read paths", summary.read_paths)
    _append_summary_section(lines, "Modified paths", summary.modified_paths)
    _append_summary_section(
        lines,
        "Recent shell commands",
        summary.recent_shell_commands,
    )
    _append_summary_section(lines, "Recent verifications", summary.recent_verifications)
    _append_summary_section(lines, "Recent failures", summary.recent_failures)
    _append_summary_section(lines, "Open questions", summary.open_questions)
    _append_summary_section(lines, "Unresolved work", summary.unresolved_work)

    return "\n".join(lines)


def build_compaction_checkpoint_messages(
    *,
    retained_runs: Sequence[SessionRunRecord],
) -> list[ModelMessage]:
    return [
        message
        for run in retained_runs
        for message in strip_internal_prompt_state(run.messages)
    ]


def strip_internal_prompt_state(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    sanitized: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            sanitized.append(message)
            continue

        kept_parts = [
            part for part in message.parts if not isinstance(part, SystemPromptPart)
        ]
        if not kept_parts:
            continue
        sanitized.append(replace(message, parts=kept_parts, instructions=None))

    return sanitized


def select_compaction_checkpoint_tail(
    retained_runs: Sequence[SessionRunRecord],
    *,
    token_budget: int,
) -> tuple[list[ModelMessage], str | None, bool]:
    if token_budget <= 0:
        return [], None, False

    flattened: list[tuple[str, ModelMessage]] = [
        (run.run_id, message)
        for run in retained_runs
        for message in strip_internal_prompt_state(run.messages)
    ]
    if not flattened:
        return [], None, False

    message_costs = [
        _estimate_message_tokens(message)
        for _run_id, message in flattened
    ]
    total_tokens = 0
    selected_start_index: int | None = None

    for start_index in range(len(flattened) - 1, -1, -1):
        total_tokens += message_costs[start_index]
        if total_tokens > token_budget:
            break

        suffix = [message for _run_id, message in flattened[start_index:]]
        if _checkpoint_tail_is_safe(suffix):
            selected_start_index = start_index

    if selected_start_index is None:
        return [], None, False

    first_kept_run_id = flattened[selected_start_index][0]
    split_within_run = not _starts_at_run_boundary(flattened, selected_start_index)
    return (
        [message for _run_id, message in flattened[selected_start_index:]],
        first_kept_run_id,
        split_within_run,
    )


def _estimate_message_tokens(message: ModelMessage) -> int:
    return max(
        1,
        -(
            -len(
                json.dumps(
                    _MODEL_MESSAGES_ADAPTER.dump_python([message], mode="json"),
                    ensure_ascii=False,
                )
            )
            // COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        ),
    )


def _checkpoint_tail_is_safe(messages: Sequence[ModelMessage]) -> bool:
    if not messages:
        return True

    first_message = messages[0]
    if isinstance(first_message, ModelRequest):
        if not any(isinstance(part, UserPromptPart) for part in first_message.parts):
            return False
    elif isinstance(first_message, ModelResponse):
        if not any(isinstance(part, ToolCallPart) for part in first_message.parts):
            return False
    else:
        return False

    seen_tool_call_ids: set[str] = set()

    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    seen_tool_call_ids.add(part.tool_call_id)
            continue

        if not isinstance(message, ModelRequest):
            return False

        for part in message.parts:
            if isinstance(part, RetryPromptPart):
                return False
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_call_id not in seen_tool_call_ids:
                return False
            seen_tool_call_ids.remove(part.tool_call_id)

    return True


def _starts_at_run_boundary(
    flattened: Sequence[tuple[str, ModelMessage]],
    start_index: int,
) -> bool:
    if start_index == 0:
        return True
    return flattened[start_index - 1][0] != flattened[start_index][0]


def _append_summary_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return

    lines.append(f"{heading}:")
    lines.extend(f"- {value}" for value in values)
