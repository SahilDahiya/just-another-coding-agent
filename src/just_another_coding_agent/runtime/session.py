from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.run_events import RunEvent
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.compaction import (
    build_session_history_processor,
    restore_in_run_compaction_from_messages,
    should_auto_compact_session,
    strip_compaction_summary_from_messages,
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.session.jsonl import (
    load_session,
    start_run_to_session,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps


async def stream_session_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    session_path: Path,
    prompt: str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    thinking: ThinkingSetting | None = None,
) -> AsyncIterator[RunEvent]:
    """Stream one run and persist session entries incrementally.

    The canonical session format only becomes loadable after terminal
    completion, when session_messages are appended. Interrupted runs remain
    visible on disk as incomplete trailing runs and load_session(...) fails
    hard instead of silently hiding them.
    """
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    loaded_session = None
    if session_path.exists():
        loaded_session = load_session(
            path=session_path,
            workspace_root=normalized_workspace_root,
        )
        if should_auto_compact_session(loaded_session):
            # Auto-compaction is pre-run session maintenance, not part of the
            # streamed run event contract. Failures here surface as an
            # exception to the caller rather than as a run_failed event.
            await summarize_and_append_compaction_to_session(
                model=model,
                path=session_path,
                workspace_root=normalized_workspace_root,
            )
            loaded_session = load_session(
                path=session_path,
                workspace_root=normalized_workspace_root,
            )
    resolved_thinking = (
        thinking
        if thinking is not None
        else (loaded_session.thinking if loaded_session is not None else None)
    )
    history_processor = (
        build_session_history_processor(loaded_session)
        if loaded_session is not None
        else None
    )
    enable_server_history = (
        loaded_session is not None and history_processor is None
    )

    agent = build_canonical_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        tool_names=tool_names,
        history_processors=(
            [history_processor] if history_processor is not None else None
        ),
    )
    run_appender = None
    authoritative_messages: list[ModelMessage] | None = None

    def _record_message_history(messages: Sequence[ModelMessage]) -> None:
        nonlocal authoritative_messages
        authoritative_messages = list(messages)

    with capture_run_messages() as messages:
        async for event in stream_run_events(
            agent=agent,
            prompt=prompt,
            message_history=(
                loaded_session.message_history if loaded_session is not None else None
            ),
            thinking=resolved_thinking,
            deps=WorkspaceDeps(workspace_root=normalized_workspace_root),
            enable_server_history=enable_server_history,
            message_history_sink=_record_message_history,
        ):
            if run_appender is None:
                run_appender = start_run_to_session(
                    path=session_path,
                    workspace_root=normalized_workspace_root,
                    run_id=event.run_id,
                    prompt=prompt,
                    thinking=resolved_thinking,
                )
            run_appender.append_event(event)
            yield event

    if run_appender is not None:
        run_appender.finalize(
            messages=restore_in_run_compaction_from_messages(
                strip_compaction_summary_from_messages(
                    authoritative_messages
                    if authoritative_messages is not None
                    else list(messages)
                )
            )
        )


__all__ = ["stream_session_run_events"]
