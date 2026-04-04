from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.compaction.boundary import run_index_for_id
from just_another_coding_agent.runtime.turn_context import (
    TurnContextBaselineDecision,
    build_runtime_context_injection_plan,
)
from just_another_coding_agent.session.replacement_history import (
    strip_internal_prompt_state,
)


def build_resume_message_history(loaded_session: LoadedSession) -> list[ModelMessage]:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return strip_internal_prompt_state(loaded_session.message_history)

    compacted_run_index = run_index_for_id(
        loaded_session,
        latest_compaction.compacted_through_run_id,
    )
    later_messages = [
        message
        for run in loaded_session.runs[compacted_run_index + 1 :]
        for message in run.messages
    ]
    return strip_internal_prompt_state(
        [*latest_compaction.replacement_messages, *later_messages]
    )


def build_runtime_framed_resume_message_history(
    loaded_session: LoadedSession | None,
    *,
    baseline_decision: TurnContextBaselineDecision | None = None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
) -> list[ModelMessage]:
    resume_history = (
        build_resume_message_history(loaded_session)
        if loaded_session is not None
        else []
    )
    injection_plan = build_runtime_context_injection_plan(
        baseline_decision=baseline_decision,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=timezone,
        thinking=thinking,
    )
    return [
        *injection_plan.before_history_messages,
        *resume_history,
        *injection_plan.after_history_messages,
    ]


__all__ = [
    "build_resume_message_history",
    "build_runtime_framed_resume_message_history",
]
