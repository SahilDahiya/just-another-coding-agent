from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    RunFailedEvent,
    RunSucceededEvent,
    SessionCompactionCompletedEvent,
    SessionCompactionStartedEvent,
    SessionCompactionWarningEvent,
    SessionLifecycleEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import build_failed_tool_activity
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.compaction import (
    build_auto_compact_session_budget_report,
    build_compaction_history_processors,
    build_resume_instructions,
    build_resume_message_history,
    restore_in_run_compaction_from_messages,
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.session.checkpoint import strip_internal_prompt_state
from just_another_coding_agent.session.jsonl import (
    load_session,
    read_session_metadata,
    start_run_to_session,
    update_session_auto_compaction_failures,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps

MAX_CONSECUTIVE_AUTO_COMPACTION_FAILURES = 3


def _strip_unresolved_tool_calls_from_messages(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    pending_tool_call_ids: set[str] = set()

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                pending_tool_call_ids.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                pending_tool_call_ids.discard(part.tool_call_id)

    if not pending_tool_call_ids:
        return list(messages)

    sanitized: list[ModelMessage] = []
    for message in messages:
        kept_parts = [
            part
            for part in message.parts
            if not (
                isinstance(part, (ToolCallPart, ToolReturnPart))
                and part.tool_call_id in pending_tool_call_ids
            )
        ]
        if not kept_parts:
            continue
        if len(kept_parts) == len(message.parts):
            sanitized.append(message)
            continue
        sanitized.append(replace(message, parts=kept_parts))

    return sanitized


def _strip_failed_correction_tail_from_messages(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    sanitized = list(messages)

    while sanitized:
        last_message = sanitized[-1]
        if not isinstance(last_message, ModelRequest):
            break

        retry_parts = [
            part for part in last_message.parts if isinstance(part, RetryPromptPart)
        ]
        if not retry_parts or len(retry_parts) != len(last_message.parts):
            break

        retry_tool_call_ids = {part.tool_call_id for part in retry_parts}
        sanitized.pop()

        if not sanitized:
            break

        previous_message = sanitized[-1]
        if not isinstance(previous_message, ModelResponse):
            break

        kept_parts = [
            part
            for part in previous_message.parts
            if not (
                isinstance(part, ToolCallPart)
                and part.tool_call_id in retry_tool_call_ids
            )
        ]

        if not kept_parts:
            sanitized.pop()
            continue

        if len(kept_parts) != len(previous_message.parts):
            sanitized[-1] = replace(previous_message, parts=kept_parts)

    return sanitized


def _sanitize_failed_run_messages(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    return _strip_failed_correction_tail_from_messages(
        _strip_unresolved_tool_calls_from_messages(messages)
    )


async def stream_session_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    session_path: Path,
    prompt: str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    thinking: ThinkingSetting | None = None,
) -> AsyncIterator[RunEvent | SessionLifecycleEvent]:
    """Stream one run and persist session entries incrementally.

    The canonical session format only becomes loadable after terminal
    completion, when session_messages are appended. Consumer abandonment or
    backend crashes before finalization remain visible on disk as incomplete
    trailing runs and load_session(...) fails hard instead of silently hiding
    them. Cancellation that unwinds through this generator is finalized as a
    terminal run_failed so future runs can resume the session cleanly.
    """
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    shell_family = detect_default_shell_family()
    loaded_session = None
    if session_path.exists():
        loaded_session = load_session(
            path=session_path,
            workspace_root=normalized_workspace_root,
        )
        compaction_budget_before = build_auto_compact_session_budget_report(
            loaded_session,
            model=model,
        )
        if compaction_budget_before.should_compact:
            metadata = read_session_metadata(
                path=session_path.with_suffix(".meta.json")
            )
            if (
                metadata.consecutive_auto_compaction_failures
                >= MAX_CONSECUTIVE_AUTO_COMPACTION_FAILURES
            ):
                raise RuntimeError(
                    "Auto-compaction blocked after repeated failures. "
                    "Start a new session or reduce context before retrying."
                )
            # Auto-compaction is pre-run session maintenance, not part of the
            # streamed run event contract. Failures here surface as an
            # exception to the caller rather than as a run_failed event.
            yield SessionCompactionStartedEvent(budget=compaction_budget_before)
            try:
                compaction_entry = await summarize_and_append_compaction_to_session(
                    model=model,
                    path=session_path,
                    workspace_root=normalized_workspace_root,
                )
            except Exception:
                update_session_auto_compaction_failures(
                    path=session_path,
                    consecutive_auto_compaction_failures=(
                        metadata.consecutive_auto_compaction_failures + 1
                    ),
                )
                raise
            update_session_auto_compaction_failures(
                path=session_path,
                consecutive_auto_compaction_failures=0,
            )
            loaded_session = load_session(
                path=session_path,
                workspace_root=normalized_workspace_root,
            )
            compaction_budget_after = build_auto_compact_session_budget_report(
                loaded_session,
                model=model,
            )
            yield SessionCompactionCompletedEvent(
                compaction_id=compaction_entry.compaction_id,
                summarized_through_run_id=compaction_entry.summarized_through_run_id,
                first_kept_run_id=compaction_entry.first_kept_run_id,
                checkpoint_through_run_id=compaction_entry.checkpoint_through_run_id,
                budget_before=compaction_budget_before,
                budget_after=compaction_budget_after,
            )
            if len(loaded_session.compactions) >= 2:
                yield SessionCompactionWarningEvent(
                    compaction_count=len(loaded_session.compactions),
                    message=(
                        "Session has been compacted multiple times; continuity "
                        "quality may degrade."
                    ),
                )
    resolved_thinking = (
        thinking
        if thinking is not None
        else (loaded_session.thinking if loaded_session is not None else None)
    )
    preexisting_history = (
        build_resume_message_history(loaded_session)
        if loaded_session is not None
        else []
    )
    preexisting_instructions = (
        build_resume_instructions(loaded_session)
        if loaded_session is not None
        else None
    )
    preexisting_history_count = len(preexisting_history)

    agent = build_canonical_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
        tool_names=tool_names,
        history_processors=build_compaction_history_processors(model=model),
    )
    run_appender = None
    authoritative_messages: list[ModelMessage] | None = None
    pending_tool_calls: dict[str, ToolCallStartedEvent] = {}
    active_run_id: str | None = None
    should_finalize = False
    failed_terminal = False

    def _record_message_history(messages: Sequence[ModelMessage]) -> None:
        nonlocal authoritative_messages
        authoritative_messages = list(messages)

    with capture_run_messages() as messages:
        try:
            async for event in stream_run_events(
                agent=agent,
                prompt=prompt,
                message_history=(preexisting_history or None),
                instructions=preexisting_instructions,
                thinking=resolved_thinking,
                deps=WorkspaceDeps(
                    workspace_root=normalized_workspace_root,
                    shell_family=shell_family,
                ),
                message_history_sink=_record_message_history,
            ):
                if run_appender is None:
                    active_run_id = event.run_id
                    run_appender = start_run_to_session(
                        path=session_path,
                        workspace_root=normalized_workspace_root,
                        shell_family=shell_family,
                        run_id=event.run_id,
                        prompt=prompt,
                        thinking=resolved_thinking,
                    )
                run_appender.append_event(event)
                if isinstance(event, ToolCallStartedEvent):
                    pending_tool_calls[event.tool_call_id] = event
                elif isinstance(event, ToolCallSucceededEvent | ToolCallFailedEvent):
                    pending_tool_calls.pop(event.tool_call_id, None)
                elif isinstance(event, RunSucceededEvent | RunFailedEvent):
                    if isinstance(event, RunFailedEvent):
                        failed_terminal = True
                    should_finalize = True
                yield event
        except (asyncio.CancelledError, KeyboardInterrupt) as error:
            if run_appender is not None and active_run_id is not None:
                error_type = type(error).__name__
                message = str(error) or "run cancelled"
                for pending_tool_call in pending_tool_calls.values():
                    run_appender.append_event(
                        ToolCallFailedEvent(
                            run_id=active_run_id,
                            tool_call_id=pending_tool_call.tool_call_id,
                            tool_name=pending_tool_call.tool_name,
                            error_type=error_type,
                            message=message,
                            activity=build_failed_tool_activity(
                                tool_name=pending_tool_call.tool_name,
                                args=pending_tool_call.args,
                                args_valid=pending_tool_call.args_valid,
                                message=message,
                                duration_ms=0,
                            ),
                        )
                    )
                pending_tool_calls.clear()
                run_appender.append_event(
                    RunFailedEvent(
                        run_id=active_run_id,
                        error_type=error_type,
                        message=message,
                    )
                )
                failed_terminal = True
                should_finalize = True
            raise
        finally:
            if run_appender is not None and should_finalize:
                finalized_messages = restore_in_run_compaction_from_messages(
                    (
                        authoritative_messages
                        if authoritative_messages is not None
                        else list(messages)[preexisting_history_count:]
                    )
                )
                if failed_terminal:
                    finalized_messages = _sanitize_failed_run_messages(
                        finalized_messages
                    )
                finalized_messages = strip_internal_prompt_state(finalized_messages)
                run_appender.finalize(messages=finalized_messages)


__all__ = ["stream_session_run_events"]
