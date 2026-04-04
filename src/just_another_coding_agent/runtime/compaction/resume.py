from __future__ import annotations

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.session import LoadedSession
from just_another_coding_agent.runtime.compaction.boundary import run_index_for_id
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


__all__ = ["build_resume_message_history"]
