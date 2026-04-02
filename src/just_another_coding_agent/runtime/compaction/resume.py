from __future__ import annotations

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.runtime.compaction.boundary import run_index_for_id
from just_another_coding_agent.session.checkpoint import (
    COMPACTION_SUMMARY_DYNAMIC_REF,
    build_compaction_summary_message,
)


def build_resume_message_history(loaded_session: LoadedSession) -> list[ModelMessage]:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return loaded_session.message_history

    checkpoint_run_index = run_index_for_id(
        loaded_session,
        latest_compaction.checkpoint_through_run_id,
    )
    later_messages = [
        message
        for run in loaded_session.runs[checkpoint_run_index + 1 :]
        for message in run.messages
    ]
    return [*latest_compaction.checkpoint_messages, *later_messages]


__all__ = [
    "COMPACTION_SUMMARY_DYNAMIC_REF",
    "build_compaction_summary_message",
    "build_resume_message_history",
]
