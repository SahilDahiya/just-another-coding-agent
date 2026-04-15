from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    RunFailedEvent,
    RunSucceededEvent,
    SessionCompactionCompletedEvent,
    SessionCompactionStartedEvent,
    SessionLifecycleEvent,
    SessionTurnContextStatusEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionHeaderEntry,
    SessionRunRecord,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.provider_readiness import (
    ProviderReadinessError,
    compute_model_readiness,
)
from just_another_coding_agent.runtime.activity import (
    PendingToolCall,
    synthesize_tool_failed_events_for_pending,
)
from just_another_coding_agent.runtime.agent import (
    build_canonical_agent,
    detect_current_timezone_label,
)
from just_another_coding_agent.runtime.compaction import (
    build_auto_compact_session_budget_report,
    build_runtime_framed_resume_message_history,
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.runtime.transcript_summary import (
    sync_run_transcript_summary_metrics,
)
from just_another_coding_agent.runtime.turn_context import (
    build_session_turn_context_entry,
    evaluate_turn_context_baseline,
)
from just_another_coding_agent.session.jsonl import (
    load_session,
    read_session_metadata,
    start_run_to_session,
    update_session_auto_compaction_failures,
)
from just_another_coding_agent.session.replacement_history import (
    sanitize_failed_run_messages,
    strip_internal_prompt_state,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import (
    RunRuntimeFrame,
    RunSessionScope,
    WorkspaceDeps,
)

MAX_CONSECUTIVE_AUTO_COMPACTION_FAILURES = 3


def _estimated_compaction_percent_saved(
    *,
    before_tokens: int,
    after_tokens: int,
) -> float:
    if before_tokens <= 0:
        return 0.0
    saved_tokens = max(0, before_tokens - after_tokens)
    return saved_tokens / before_tokens


def _build_loaded_session_after_success(
    *,
    loaded_session: LoadedSession | None,
    workspace_root: str,
    shell_family,
    run_id: str,
    prompt: str,
    thinking: ThinkingSetting | None,
    messages: Sequence[ModelMessage],
    turn_context,
) -> LoadedSession:
    if loaded_session is None:
        header = SessionHeaderEntry(
            workspace_root=workspace_root,
            shell_family=shell_family,
        )
        runs: list[SessionRunRecord] = []
        compactions = []
    else:
        header = loaded_session.header
        runs = list(loaded_session.runs)
        compactions = list(loaded_session.compactions)

    runs.append(
        SessionRunRecord(
            run_id=run_id,
            prompt=prompt,
            thinking=thinking,
            messages=list(messages),
            events=[],
        )
    )
    return LoadedSession(
        header=header,
        fork=loaded_session.fork if loaded_session is not None else None,
        name=loaded_session.name if loaded_session is not None else None,
        runs=runs,
        latest_turn_context=turn_context,
        has_persisted_turn_context_history=True,
        compactions=compactions,
    )


def _estimate_next_request_context_window_used(
    *,
    loaded_session: LoadedSession | None,
    model: Any,
    workspace_root: str,
    current_date: date,
    shell_family,
    thinking: ThinkingSetting | None,
    run_id: str,
    prompt: str,
    messages: Sequence[ModelMessage],
    turn_context,
) -> float | None:
    projected_session = _build_loaded_session_after_success(
        loaded_session=loaded_session,
        workspace_root=workspace_root,
        shell_family=shell_family,
        run_id=run_id,
        prompt=prompt,
        thinking=thinking,
        messages=messages,
        turn_context=turn_context,
    )
    report = build_auto_compact_session_budget_report(
        projected_session,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=thinking,
    )
    if report.context_window_tokens is None or report.context_window_tokens <= 0:
        return None
    return round(report.estimated_pre_run_tokens / report.context_window_tokens, 3)


async def stream_session_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    session_path: Path,
    prompt: str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    thinking: ThinkingSetting | None = None,
    activate_steer_boundary: (
        Callable[[Callable[[list[str]], None]], Awaitable[None]]
    ) | None = None,
    submit_steer_boundary: Callable[[], Awaitable[None]] | None = None,
    deactivate_steer_boundary: Callable[[], Awaitable[None]] | None = None,
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
    current_date = date.today()
    current_timezone = detect_current_timezone_label()
    if isinstance(model, str):
        readiness = compute_model_readiness(model)
        if not readiness.configured:
            raise ProviderReadinessError(
                f"{readiness.provider} is not ready: {readiness.reason}"
            )
    loaded_session = None
    if session_path.exists():
        loaded_session = load_session(
            path=session_path,
            workspace_root=normalized_workspace_root,
        )
    resolved_thinking = (
        thinking
        if thinking is not None
        else (loaded_session.thinking if loaded_session is not None else None)
    )
    turn_context_baseline = None
    if loaded_session is not None:
        compaction_budget_before = build_auto_compact_session_budget_report(
            loaded_session,
            model=model,
            workspace_root=normalized_workspace_root,
            current_date=current_date,
            shell_family=shell_family,
            thinking=resolved_thinking,
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
                workspace_root=normalized_workspace_root,
                current_date=current_date,
                shell_family=shell_family,
                thinking=resolved_thinking,
            )
            estimated_tokens_saved = max(
                0,
                compaction_budget_before.estimated_resume_message_tokens
                - compaction_budget_after.estimated_resume_message_tokens,
            )
            estimated_headroom_gain_tokens = None
            if (
                compaction_budget_before.estimated_post_compaction_headroom_tokens
                is not None
                and compaction_budget_after.estimated_post_compaction_headroom_tokens
                is not None
            ):
                estimated_headroom_gain_tokens = (
                    compaction_budget_after.estimated_post_compaction_headroom_tokens
                    - compaction_budget_before.estimated_post_compaction_headroom_tokens
                )
            yield SessionCompactionCompletedEvent(
                compaction_id=compaction_entry.compaction_id,
                compacted_through_run_id=compaction_entry.compacted_through_run_id,
                budget_before=compaction_budget_before,
                budget_after=compaction_budget_after,
                estimated_tokens_saved=estimated_tokens_saved,
                estimated_percent_saved=_estimated_compaction_percent_saved(
                    before_tokens=compaction_budget_before.estimated_resume_message_tokens,
                    after_tokens=compaction_budget_after.estimated_resume_message_tokens,
                ),
                estimated_headroom_gain_tokens=estimated_headroom_gain_tokens,
            )
    if loaded_session is not None:
        turn_context_baseline = evaluate_turn_context_baseline(
            entry=loaded_session.latest_turn_context,
            model=model,
            workspace_root=normalized_workspace_root,
            current_date=current_date,
            shell_family=shell_family,
            thinking=resolved_thinking,
            has_persisted_history=loaded_session.has_persisted_turn_context_history,
        )
        yield SessionTurnContextStatusEvent(
            status=turn_context_baseline.status,
            reason=turn_context_baseline.reason,
            persisted_run_id=(
                loaded_session.latest_turn_context.run_id
                if loaded_session.latest_turn_context is not None
                else None
            ),
        )
    preexisting_history = build_runtime_framed_resume_message_history(
        loaded_session,
        baseline_decision=turn_context_baseline,
        model=model,
        workspace_root=normalized_workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        thinking=resolved_thinking,
        tool_names=tool_names,
    )
    agent = build_canonical_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        tool_names=tool_names,
    )
    run_appender = None
    run_turn_context = None
    authoritative_messages: list[ModelMessage] | None = None
    pending_tool_calls: dict[str, ToolCallStartedEvent] = {}
    active_run_id: str | None = None
    should_finalize = False
    failed_terminal = False

    def _record_message_history(messages: Sequence[ModelMessage]) -> None:
        nonlocal authoritative_messages
        authoritative_messages = list(messages)

    try:
        stream_run_kwargs = dict(
            agent=agent,
            prompt=prompt,
            message_history=(preexisting_history or None),
            instructions=None,
            thinking=resolved_thinking,
            deps=WorkspaceDeps(
                workspace_root=normalized_workspace_root,
                shell_family=shell_family,
                session_scope=RunSessionScope(session_id=session_path.stem),
                run_frame=RunRuntimeFrame(
                    model=model,
                    current_date=current_date,
                    timezone=current_timezone,
                    thinking=resolved_thinking,
                ),
            ),
            message_history_sink=_record_message_history,
            available_tool_names=tool_names,
        )
        if (
            activate_steer_boundary is not None
            and submit_steer_boundary is not None
            and deactivate_steer_boundary is not None
        ):
            stream_run_kwargs["activate_steer_boundary"] = (
                activate_steer_boundary
            )
            stream_run_kwargs["submit_steer_boundary"] = (
                submit_steer_boundary
            )
            stream_run_kwargs["deactivate_steer_boundary"] = (
                deactivate_steer_boundary
            )
        async for event in stream_run_events(**stream_run_kwargs):
            if run_appender is None:
                active_run_id = event.run_id
                run_turn_context = build_session_turn_context_entry(
                    run_id=event.run_id,
                    model=model,
                    workspace_root=normalized_workspace_root,
                    current_date=current_date,
                    shell_family=shell_family,
                    thinking=resolved_thinking,
                )
                run_appender = start_run_to_session(
                    path=session_path,
                    workspace_root=normalized_workspace_root,
                    shell_family=shell_family,
                    run_id=event.run_id,
                    prompt=prompt,
                    thinking=resolved_thinking,
                )
            if isinstance(event, RunSucceededEvent):
                if authoritative_messages is None:
                    raise RuntimeError(
                        "Cannot finalize successful run without authoritative "
                        "messages: stream_run_events must fire "
                        "message_history_sink before yielding RunSucceededEvent"
                    )
                finalized_messages = strip_internal_prompt_state(
                    list(authoritative_messages)
                )
                event = event.model_copy(
                    update={
                        "next_request_context_window_used": (
                            _estimate_next_request_context_window_used(
                                loaded_session=loaded_session,
                                model=model,
                                workspace_root=str(normalized_workspace_root),
                                current_date=current_date,
                                shell_family=shell_family,
                                thinking=resolved_thinking,
                                run_id=event.run_id,
                                prompt=prompt,
                                messages=finalized_messages,
                                turn_context=run_turn_context,
                            )
                        )
                    }
                )
                event = sync_run_transcript_summary_metrics(event)
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
            for tool_failed_event in synthesize_tool_failed_events_for_pending(
                run_id=active_run_id,
                pending=(
                    PendingToolCall(
                        tool_call_id=pending_tool_call.tool_call_id,
                        tool_name=pending_tool_call.tool_name,
                        args=pending_tool_call.args,
                        args_valid=pending_tool_call.args_valid,
                        started_at=0,
                    )
                    for pending_tool_call in pending_tool_calls.values()
                ),
                error_type=error_type,
                message=message,
            ):
                run_appender.append_event(tool_failed_event)
                yield tool_failed_event
            pending_tool_calls.clear()
            run_failed_event = RunFailedEvent(
                run_id=active_run_id,
                error_type=error_type,
                message=message,
            )
            run_appender.append_event(run_failed_event)
            yield run_failed_event
            failed_terminal = True
            should_finalize = True
        raise
    except Exception as error:
        # Any non-cancellation exception inside the run loop must still
        # produce a terminal event for the LIVE consumer (e.g. the TUI)
        # so it knows the run has ended. Without this, an unexpected
        # exception leaves the consumer stuck on "running" forever.
        #
        # We deliberately do NOT call run_appender.append_event() here:
        # the persistence contract is that incomplete runs leave the
        # JSONL in an explicitly-broken state so load_session can detect
        # them and refuse to resume. We honor that contract — only the
        # in-memory event stream is finalized.
        if active_run_id is not None:
            error_type = type(error).__name__
            message = str(error) or error_type
            for tool_failed_event in synthesize_tool_failed_events_for_pending(
                run_id=active_run_id,
                pending=(
                    PendingToolCall(
                        tool_call_id=pending_tool_call.tool_call_id,
                        tool_name=pending_tool_call.tool_name,
                        args=pending_tool_call.args,
                        args_valid=pending_tool_call.args_valid,
                        started_at=0,
                    )
                    for pending_tool_call in pending_tool_calls.values()
                ),
                error_type=error_type,
                message=message,
            ):
                yield tool_failed_event
            pending_tool_calls.clear()
            run_failed_event = RunFailedEvent(
                run_id=active_run_id,
                error_type=error_type,
                message=message,
            )
            yield run_failed_event
        raise
    finally:
        try:
            if run_appender is not None and should_finalize:
                if authoritative_messages is None:
                    # Invariant: stream_run_events must fire message_history_sink
                    # on every terminal path (success, explicit RunFailedEvent,
                    # cancellation/BaseException) before reaching finalization.
                    # If we get here with authoritative_messages unset, that's a
                    # bug in run.py's terminal-path sink-firing discipline and we
                    # fail hard rather than persist an empty transcript. If this
                    # ever fires during exception propagation, it will chain onto
                    # the in-flight exception via __context__, preserving the
                    # original cause.
                    raise RuntimeError(
                        "Cannot finalize run without authoritative messages: "
                        "stream_run_events must fire message_history_sink on "
                        "every terminal path before session finalization runs"
                    )
                finalized_messages: list[ModelMessage] = list(authoritative_messages)
                if failed_terminal:
                    finalized_messages = sanitize_failed_run_messages(
                        finalized_messages
                    )
                finalized_messages = strip_internal_prompt_state(finalized_messages)
                run_appender.finalize(
                    messages=finalized_messages,
                    turn_context=run_turn_context,
                )
        finally:
            if run_appender is not None:
                run_appender.close()


__all__ = ["stream_session_run_events"]
