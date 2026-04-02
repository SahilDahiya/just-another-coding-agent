from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
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
    build_resume_message_history,
    restore_in_run_compaction_from_messages,
    should_auto_compact_session,
    strip_compaction_summary_from_messages,
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.session.jsonl import (
    load_session,
    read_session_metadata,
    start_run_to_session,
    update_session_auto_compaction_failures,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])
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


def _strip_resumed_history_prefix(
    messages: Sequence[ModelMessage],
    *,
    loaded_session: Any,
) -> list[ModelMessage]:
    if loaded_session is None:
        return list(messages)

    resumed_history = strip_compaction_summary_from_messages(
        build_resume_message_history(loaded_session)
    )
    if not resumed_history:
        return list(messages)

    candidate_messages = list(messages)
    normalized_resumed_history = _normalize_messages_for_prefix_match(
        resumed_history
    )

    for prefix_end in range(1, len(candidate_messages) + 1):
        candidate_prefix = candidate_messages[:prefix_end]
        if (
            _normalize_messages_for_prefix_match(candidate_prefix)
            == normalized_resumed_history
        ):
            return candidate_messages[prefix_end:]

    return candidate_messages


def _normalize_messages_for_prefix_match(
    messages: Sequence[ModelMessage],
) -> list[object]:
    coalesced_messages = _coalesce_messages_for_prefix_match(messages)
    return _strip_run_ids_from_json_value(
        _MODEL_MESSAGES_ADAPTER.dump_python(coalesced_messages, mode="json")
    )


def _coalesce_messages_for_prefix_match(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    coalesced: list[ModelMessage] = []

    for message in messages:
        if not coalesced or type(coalesced[-1]) is not type(message):
            coalesced.append(message)
            continue

        previous_message = coalesced[-1]
        coalesced[-1] = replace(
            previous_message,
            parts=[*previous_message.parts, *message.parts],
        )

    return coalesced


def _strip_run_ids_from_json_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_run_ids_from_json_value(item)
            for key, item in value.items()
            if key not in {"run_id", "timestamp", "instructions"}
        }
    if isinstance(value, list):
        return [_strip_run_ids_from_json_value(item) for item in value]
    return value


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
        if should_auto_compact_session(loaded_session, model=model):
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
            yield SessionCompactionStartedEvent()
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
            yield SessionCompactionCompletedEvent(
                compaction_id=compaction_entry.compaction_id,
                summarized_through_run_id=compaction_entry.summarized_through_run_id,
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

    agent = build_canonical_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
        tool_names=tool_names,
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
                message_history=(
                    build_resume_message_history(loaded_session)
                    if loaded_session is not None
                    else None
                ),
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
                    strip_compaction_summary_from_messages(
                        authoritative_messages
                        if authoritative_messages is not None
                        else list(messages)
                    )
                )
                finalized_messages = _strip_resumed_history_prefix(
                    finalized_messages,
                    loaded_session=loaded_session,
                )
                if failed_terminal:
                    finalized_messages = _sanitize_failed_run_messages(
                        finalized_messages
                    )
                run_appender.finalize(messages=finalized_messages)


__all__ = ["stream_session_run_events"]
