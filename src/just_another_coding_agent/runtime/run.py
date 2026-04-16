from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import (
    Agent,
    CallToolsNode,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequestNode,
    PartDeltaEvent,
    PartStartEvent,
    capture_run_messages,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_graph import End

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    InRunCompactionCompletedEvent,
    JsonValue,
    RunEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import (
    PendingToolCall,
    build_failed_tool_activity,
    build_started_tool_activity,
    build_succeeded_tool_activity,
    build_updated_tool_activity,
    synthesize_tool_failed_events_for_pending,
)
from just_another_coding_agent.runtime.agent import (
    CANONICAL_AGENT_TOOL_CORRECTION_RETRIES,
)
from just_another_coding_agent.runtime.compaction.constants import (
    SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS,
)
from just_another_coding_agent.runtime.compaction.trigger import (
    LastResponseUsageSnapshot,
    check_in_run_compaction_needed,
)
from just_another_coding_agent.runtime.models import (
    build_canonical_model_settings,
    get_model_context_window_tokens,
)
from just_another_coding_agent.runtime.prompt_layers import build_prompt_context_layers
from just_another_coding_agent.runtime.recovery import should_retry_run_error
from just_another_coding_agent.runtime.spans import (
    _finish_all_tool_spans,
    _finish_tool_span,
    _set_span_attributes,
    _start_model_request_span,
    _start_run_span,
    _start_tool_span,
)
from just_another_coding_agent.runtime.transcript_summary import (
    build_run_transcript_summary,
)
from just_another_coding_agent.session.replacement_history import (
    build_in_run_truncated_history,
    reconcile_synthetic_prompt_counts,
    sanitize_failed_run_messages,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingToolCall:
    tool_name: str
    args: JsonValue | None
    args_valid: bool | None
    started_at: float


@dataclass(frozen=True)
class _NormalizedToolArgs:
    args: JsonValue | None
    args_valid: bool | None


@dataclass(frozen=True)
class _QueuedToolUpdate:
    tool_call_id: str
    tool_name: str
    partial_result: JsonValue | None


class _RestartRunWithCorrection(Exception):
    def __init__(
        self,
        message: str,
        pre_raise_events: list[RunEvent] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.pre_raise_events: list[RunEvent] = pre_raise_events or []


class _InRunCompactRequested(Exception):
    pass


MAX_IN_RUN_COMPACT_FAILURES = 3
IN_RUN_COMPACTION_CONTINUATION_PROMPT = (
    "Continue the task. Earlier turns were truncated to fit the context window; "
    "the recent turns and your original instructions are preserved above."
)


def _build_tool_updated_event(
    *,
    run_id: str,
    queued_update: _QueuedToolUpdate,
    pending_tool_call: _PendingToolCall,
) -> ToolCallUpdatedEvent:
    return ToolCallUpdatedEvent(
        run_id=run_id,
        tool_call_id=queued_update.tool_call_id,
        tool_name=pending_tool_call.tool_name,
        partial_result=queued_update.partial_result,
        activity=build_updated_tool_activity(
            tool_name=pending_tool_call.tool_name,
            args=pending_tool_call.args,
            args_valid=pending_tool_call.args_valid,
            partial_result=queued_update.partial_result,
            duration_ms=_duration_ms_since(pending_tool_call.started_at),
        ),
    )


def _drain_buffered_tool_updates(
    *,
    run_id: str,
    pending_tool_call: _PendingToolCall,
    tool_call_id: str,
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]],
) -> list[ToolCallUpdatedEvent]:
    return [
        _build_tool_updated_event(
            run_id=run_id,
            queued_update=queued_update,
            pending_tool_call=pending_tool_call,
        )
        for queued_update in buffered_tool_updates.pop(tool_call_id, [])
    ]


def _raise_if_buffered_tool_updates_remain(
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]],
) -> None:
    if not buffered_tool_updates:
        return
    stale_tool_call_id = next(iter(buffered_tool_updates))
    raise RuntimeError(
        "Tool update must match a pending tool_call_started: "
        f"{stale_tool_call_id}"
    )


def _build_unbounded_usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=None,
        tool_calls_limit=None,
        input_tokens_limit=None,
        output_tokens_limit=None,
        total_tokens_limit=None,
    )


def _bind_workspace_deps_to_run(
    *,
    deps: WorkspaceDeps,
    run_id: str,
    tool_update_sink: Callable[[str, str, JsonValue | None], Awaitable[None]] | None,
) -> WorkspaceDeps:
    return replace(
        deps,
        session_scope=replace(
            deps.session_scope,
            run_id=run_id,
        ),
        tool_update_sink=tool_update_sink,
    )


def _build_run_succeeded_event(
    *,
    run_id: str,
    agent: Agent[Any, Any],
    output: str,
    usage,
) -> RunSucceededEvent:
    context_limit = _get_context_window_tokens(agent)
    return RunSucceededEvent(
        run_id=run_id,
        output_text=output,
        input_tokens=usage.input_tokens or None,
        output_tokens=usage.output_tokens or None,
        total_tokens=usage.total_tokens or None,
        context_window_used=(
            round(usage.total_tokens / context_limit, 3)
            if context_limit and usage.total_tokens
            else None
        ),
    )


def _call_tools_node_has_tool_calls(node: CallToolsNode[Any, Any]) -> bool:
    return any(isinstance(part, ToolCallPart) for part in node.model_response.parts)


# ---------------------------------------------------------------------------
# Extracted helpers for _stream_run_events inner-loop event processing.
# Each returns a list of RunEvent objects that the caller yields, plus
# signals for control flow (terminal return, duplicate skip).
# ---------------------------------------------------------------------------


@dataclass
class _ToolCallResult:
    """Result of processing a single FunctionToolCallEvent."""

    events: list[RunEvent]
    is_duplicate: bool = False
    terminal: bool = False  # caller should return after yielding events


def _process_tool_call_event(
    *,
    event: FunctionToolCallEvent,
    run_id: str,
    session_id: str | None,
    pending_tool_calls: dict[str, _PendingToolCall],
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]],
    completed_tool_calls: dict[str, str],
    active_tool_spans: dict[str, Any],
    available_tool_names: Sequence[str],
    correction_attempts: int,
    run_span: Any,
) -> _ToolCallResult:
    """Process a FunctionToolCallEvent, returning events to yield.

    Mutates ``pending_tool_calls``, ``active_tool_spans``,
    ``completed_tool_calls``, and ``buffered_tool_updates`` in place.
    May raise ``_RestartRunWithCorrection``.
    """
    normalized_args = _normalize_tool_args(
        event.part.args, args_valid=event.args_valid,
    )
    args = normalized_args.args
    args_valid = normalized_args.args_valid

    # --- Duplicate tool-call ID: skip silently. -------------------------
    existing = pending_tool_calls.get(event.tool_call_id)
    if existing is not None:
        if (
            existing.tool_name != event.part.tool_name
            or existing.args != args
        ):
            raise RuntimeError(
                f"Duplicate tool call mismatch for "
                f"tool_call_id {event.tool_call_id!r}"
            )
        return _ToolCallResult(events=[], is_duplicate=True)

    # --- Register new pending call. ------------------------------------
    pending = _PendingToolCall(
        tool_name=event.part.tool_name,
        args=args,
        args_valid=args_valid,
        started_at=monotonic(),
    )
    pending_tool_calls[event.tool_call_id] = pending
    active_tool_spans[event.tool_call_id] = _start_tool_span(
        run_id=run_id,
        tool_call_id=event.tool_call_id,
        tool_name=event.part.tool_name,
        args_valid=args_valid,
        session_id=session_id,
    )

    events: list[RunEvent] = [
        ToolCallStartedEvent(
            run_id=run_id,
            tool_call_id=event.tool_call_id,
            tool_name=event.part.tool_name,
            args=args,
            args_valid=args_valid,
            activity=build_started_tool_activity(
                tool_name=event.part.tool_name,
                args=args,
                args_valid=args_valid,
            ),
        ),
    ]
    events.extend(
        _drain_buffered_tool_updates(
            run_id=run_id,
            pending_tool_call=pending,
            tool_call_id=event.tool_call_id,
            buffered_tool_updates=buffered_tool_updates,
        )
    )

    # --- Malformed tool correction. ------------------------------------
    malformed_message = _malformed_tool_correction_message(
        tool_name=event.part.tool_name,
        raw_args=event.part.args,
        args_valid=args_valid,
        available_tool_names=available_tool_names,
    )
    if malformed_message is None:
        return _ToolCallResult(events=events)

    pending_tool_call = pending_tool_calls.pop(event.tool_call_id)
    completed_tool_calls[event.tool_call_id] = pending_tool_call.tool_name

    if correction_attempts >= CANONICAL_AGENT_TOOL_CORRECTION_RETRIES:
        exhausted_message = _tool_correction_exhausted_message(
            pending_tool_call.tool_name
        )
        events.append(
            ToolCallFailedEvent(
                run_id=run_id,
                tool_call_id=event.tool_call_id,
                tool_name=pending_tool_call.tool_name,
                error_type="RuntimeError",
                message=exhausted_message,
                activity=build_failed_tool_activity(
                    tool_name=pending_tool_call.tool_name,
                    args=pending_tool_call.args,
                    args_valid=pending_tool_call.args_valid,
                    message=exhausted_message,
                    duration_ms=_duration_ms_since(
                        pending_tool_call.started_at
                    ),
                ),
            )
        )
        _finish_tool_span(
            active_tool_spans=active_tool_spans,
            tool_call_id=event.tool_call_id,
            status="failed",
            duration_ms=_duration_ms_since(pending_tool_call.started_at),
            error_type="RuntimeError",
            error_message=exhausted_message,
        )
        _set_span_attributes(
            run_span,
            {
                "jaca.run.status": "failed",
                "jaca.run.error_type": "RuntimeError",
                "jaca.run.error_message": exhausted_message,
            },
        )
        events.append(
            RunFailedEvent(
                run_id=run_id,
                error_type="RuntimeError",
                message=exhausted_message,
            )
        )
        return _ToolCallResult(events=events, terminal=True)

    retry_result = _tool_error_result(
        error_type="RetryPromptPart",
        message=malformed_message,
    )
    events.append(
        ToolCallSucceededEvent(
            run_id=run_id,
            tool_call_id=event.tool_call_id,
            tool_name=pending_tool_call.tool_name,
            result=retry_result,
            activity=build_succeeded_tool_activity(
                tool_name=pending_tool_call.tool_name,
                args=pending_tool_call.args,
                args_valid=pending_tool_call.args_valid,
                result=retry_result,
                duration_ms=_duration_ms_since(
                    pending_tool_call.started_at
                ),
            ),
        )
    )
    _finish_tool_span(
        active_tool_spans=active_tool_spans,
        tool_call_id=event.tool_call_id,
        status="succeeded",
        duration_ms=_duration_ms_since(pending_tool_call.started_at),
    )
    raise _RestartRunWithCorrection(
        malformed_message, pre_raise_events=events,
    )


def _process_tool_result_event(
    *,
    event: FunctionToolResultEvent,
    run_id: str,
    pending_tool_calls: dict[str, _PendingToolCall],
    completed_tool_calls: dict[str, str],
    active_tool_spans: dict[str, Any],
) -> list[RunEvent]:
    """Process a FunctionToolResultEvent, returning events to yield.

    Mutates ``pending_tool_calls``, ``completed_tool_calls``, and
    ``active_tool_spans`` in place.  May raise
    ``_RestartRunWithCorrection`` for RetryPromptPart results.
    """
    if isinstance(event.result, RetryPromptPart):
        pending_tool_call = _resolve_pending_tool_call(
            pending_tool_calls=pending_tool_calls,
            tool_call_id=event.tool_call_id,
            result_tool_name=event.result.tool_name,
        )
        completed_tool_calls[
            event.tool_call_id
        ] = pending_tool_call.tool_name
        retry_message = _retry_prompt_message(event.result)
        retry_result = _tool_error_result(
            error_type="RetryPromptPart",
            message=retry_message,
        )
        pre_events: list[RunEvent] = [
            ToolCallSucceededEvent(
                run_id=run_id,
                tool_call_id=event.tool_call_id,
                tool_name=pending_tool_call.tool_name,
                result=retry_result,
                activity=build_succeeded_tool_activity(
                    tool_name=pending_tool_call.tool_name,
                    args=pending_tool_call.args,
                    args_valid=pending_tool_call.args_valid,
                    result=retry_result,
                    duration_ms=_duration_ms_since(
                        pending_tool_call.started_at
                    ),
                ),
            )
        ]
        _finish_tool_span(
            active_tool_spans=active_tool_spans,
            tool_call_id=event.tool_call_id,
            status="succeeded",
            duration_ms=_duration_ms_since(pending_tool_call.started_at),
        )
        raise _RestartRunWithCorrection(
            retry_message, pre_raise_events=pre_events,
        )

    # --- Normal tool result. -------------------------------------------
    pending_tool_call = _resolve_pending_tool_call(
        pending_tool_calls=pending_tool_calls,
        tool_call_id=event.tool_call_id,
        result_tool_name=event.result.tool_name,
    )
    completed_tool_calls[event.tool_call_id] = pending_tool_call.tool_name
    result = _normalize_json_value(event.result.content)
    result_metadata = getattr(event.result, "metadata", None)
    events = [
        ToolCallSucceededEvent(
            run_id=run_id,
            tool_call_id=event.tool_call_id,
            tool_name=pending_tool_call.tool_name,
            result=result,
            activity=build_succeeded_tool_activity(
                tool_name=pending_tool_call.tool_name,
                args=pending_tool_call.args,
                args_valid=pending_tool_call.args_valid,
                result=result,
                result_metadata=result_metadata,
                duration_ms=_duration_ms_since(
                    pending_tool_call.started_at
                ),
            ),
        )
    ]
    _finish_tool_span(
        active_tool_spans=active_tool_spans,
        tool_call_id=event.tool_call_id,
        status="succeeded",
        duration_ms=_duration_ms_since(pending_tool_call.started_at),
    )
    return events


@dataclass
class _InRunCompactionOutcome:
    """Result of attempting in-run compaction."""

    succeeded: bool
    new_message_history: list[ModelMessage] | None = None
    new_carried_messages: list[ModelMessage] | None = None
    new_synthetic_prompt_counts: dict[str, int] | None = None
    new_prompt: str | None = None
    event: InRunCompactionCompletedEvent | None = None


def _attempt_in_run_compaction(
    *,
    run_id: str,
    live_messages: list[ModelMessage],
    synthetic_prompt_counts: dict[str, int],
    agent: Any,
    deps: WorkspaceDeps | None,
    thinking: ThinkingSetting | None,
    in_run_compact_failures: int,
    active_tool_spans: dict[str, Any],
) -> _InRunCompactionOutcome:
    """Try to compact the live message history mid-run.

    Returns an outcome indicating success or failure. On failure the
    caller should increment ``in_run_compact_failures`` and continue.
    On success the caller should apply the new state and yield the
    event.
    """
    try:
        compact_model = getattr(agent, "model", None)
        replacement_tail = build_in_run_truncated_history(
            messages=live_messages,
            model=compact_model,
            token_budget=SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS,
            synthetic_prompt_counts=synthetic_prompt_counts,
        )
        initial_context: list[ModelMessage] = []
        if isinstance(deps, WorkspaceDeps):
            run_frame = deps.run_frame
            prompt_context = build_prompt_context_layers(
                model=compact_model,
                workspace_root=deps.workspace_root,
                shell_family=deps.shell_family,
                current_date=(
                    run_frame.current_date if run_frame else None
                ),
                timezone=(
                    run_frame.timezone if run_frame else None
                ),
                thinking=(
                    run_frame.thinking if run_frame else thinking
                ),
            )
            initial_context = [
                *prompt_context.before_history_messages,
            ]
        replacement = [*initial_context, *replacement_tail]
    except Exception:
        logger.warning(
            "In-run compaction failed: run_id=%s attempt=%s",
            run_id,
            in_run_compact_failures + 1,
            exc_info=True,
        )
        return _InRunCompactionOutcome(succeeded=False)

    new_history = replacement
    new_synth = reconcile_synthetic_prompt_counts(
        synthetic_prompt_counts, new_history
    )
    new_carried = list(new_history)
    new_prompt = IN_RUN_COMPACTION_CONTINUATION_PROMPT
    new_synth[IN_RUN_COMPACTION_CONTINUATION_PROMPT] = (
        new_synth.get(IN_RUN_COMPACTION_CONTINUATION_PROMPT, 0) + 1
    )
    _finish_all_tool_spans(
        active_tool_spans=active_tool_spans,
        status="aborted",
        error_type="InRunCompactionCompletedEvent",
        error_message="In-run compaction restarted the run",
    )
    logger.info(
        "In-run compaction completed: run_id=%s "
        "live_messages=%d replacement_messages=%d",
        run_id,
        len(live_messages),
        len(replacement),
    )
    return _InRunCompactionOutcome(
        succeeded=True,
        new_message_history=new_history,
        new_carried_messages=new_carried,
        new_synthetic_prompt_counts=new_synth,
        new_prompt=new_prompt,
        event=InRunCompactionCompletedEvent(
            run_id=run_id,
            live_message_count=len(live_messages),
            replacement_message_count=len(replacement),
        ),
    )


async def _stream_run_events(
    *,
    agent: Agent[Any, Any],
    run_id: str,
    prompt: str,
    message_history: Sequence[ModelMessage] | None = None,
    instructions: str | None = None,
    thinking: ThinkingSetting | None = None,
    deps: WorkspaceDeps | None = None,
    message_history_sink: Callable[[Sequence[ModelMessage]], None] | None = None,
    available_tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    activate_steer_boundary: (
        Callable[[Callable[[list[str]], None]], Awaitable[None]]
    ) | None = None,
    submit_steer_boundary: Callable[[], Awaitable[None]] | None = None,
    deactivate_steer_boundary: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[RunEvent]:
    """Translate one PydanticAI run into the canonical streamed event contract.

    Runtime exceptions before a terminal run event are converted into canonical
    failure events by design. Any exception after terminal success is invalid
    state and is raised.
    """
    steer_enabled = activate_steer_boundary is not None
    recovery_attempts = 0
    correction_attempts = 0
    current_prompt = prompt
    current_message_history = list(message_history or [])
    carried_messages: list[ModelMessage] = []
    pending_tool_calls: dict[str, _PendingToolCall] = {}
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]] = {}
    completed_tool_calls: dict[str, str] = {}
    in_run_compact_failures = 0
    last_response_usage: LastResponseUsageSnapshot | None = None
    synthetic_prompt_counts: dict[str, int] = {}
    active_tool_spans: dict[str, Any] = {}
    session_id = (
        deps.session_scope.session_id
        if isinstance(deps, WorkspaceDeps)
        else None
    )

    with _start_run_span(
        run_id=run_id,
        prompt=prompt,
        available_tool_names=available_tool_names,
        session_id=session_id,
    ) as run_span:
        yield RunStartedEvent(run_id=run_id)

        while True:
            saw_streamed_event = False
            terminal_emitted = False
            compaction_restarting = False
            attempt_history_count = len(current_message_history)
            model_requests_in_attempt = 0
            queue: asyncio.Queue[object] = asyncio.Queue()
            queued_deps = deps
            if isinstance(deps, WorkspaceDeps):
                async def _queue_tool_update(
                    tool_call_id: str,
                    tool_name: str,
                    partial_result: JsonValue | None,
                ) -> None:
                    await queue.put(
                        _QueuedToolUpdate(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            partial_result=partial_result,
                        )
                    )
                queued_deps = _bind_workspace_deps_to_run(
                    deps=deps,
                    run_id=run_id,
                    tool_update_sink=_queue_tool_update,
                )
            else:
                queue = asyncio.Queue()

            try:
                with (
                    agent.parallel_tool_call_execution_mode("parallel"),
                    capture_run_messages() as captured_messages,
                ):
                    async with agent.iter(
                        current_prompt,
                        message_history=(current_message_history or None),
                        instructions=instructions,
                        deps=queued_deps,
                        model_settings=build_canonical_model_settings(
                            model=getattr(agent, "model", None),
                            thinking=thinking,
                        ),
                        usage_limits=_build_unbounded_usage_limits(),
                    ) as agent_run:
                        node = agent_run.next_node
                        while True:
                            if isinstance(node, ModelRequestNode):
                                model_requests_in_attempt += 1
                                with _start_model_request_span(
                                    agent=agent,
                                    run_id=run_id,
                                    request_index=model_requests_in_attempt,
                                    session_id=session_id,
                                ) as model_request_span:
                                    try:
                                        if (
                                            in_run_compact_failures
                                            < MAX_IN_RUN_COMPACT_FAILURES
                                        ):
                                            captured_now = list(captured_messages)
                                            live_messages = [
                                                *current_message_history,
                                                *captured_now[attempt_history_count:],
                                            ]
                                            is_first_request_of_iter = (
                                                model_requests_in_attempt == 1
                                            )
                                            pending_prompt_for_check = (
                                                current_prompt
                                                if is_first_request_of_iter
                                                else None
                                            )
                                            if live_messages and check_in_run_compaction_needed(
                                                live_messages,
                                                model=getattr(agent, "model", None),
                                                last_response_usage=last_response_usage,
                                                pending_prompt=pending_prompt_for_check,
                                            ):
                                                raise _InRunCompactRequested()
                                        async with node.stream(agent_run.ctx) as stream:
                                            async for event in stream:
                                                saw_streamed_event = True
                                                text_delta = _extract_text_delta(event)
                                                if text_delta is not None:
                                                    yield AssistantTextDeltaEvent(
                                                        run_id=run_id,
                                                        delta=text_delta,
                                                    )
                                        captured_now = list(captured_messages)
                                        if captured_now and isinstance(
                                            captured_now[-1], ModelResponse
                                        ):
                                            last_resp = captured_now[-1]
                                            usage = getattr(last_resp, "usage", None)
                                            input_tokens = (
                                                getattr(usage, "input_tokens", 0)
                                                if usage
                                                else 0
                                            )
                                            output_tokens = (
                                                getattr(usage, "output_tokens", 0)
                                                if usage
                                                else 0
                                            )
                                            if input_tokens:
                                                last_response_usage = LastResponseUsageSnapshot(
                                                    input_tokens=input_tokens,
                                                    output_tokens=output_tokens,
                                                    messages_prefix_count=(
                                                        len(current_message_history)
                                                        + len(captured_now)
                                                    ),
                                                )
                                            _set_span_attributes(
                                                model_request_span,
                                                {
                                                    "jaca.model_request.status": "succeeded",
                                                    "jaca.model_request.input_tokens": input_tokens,
                                                    "jaca.model_request.output_tokens": output_tokens,
                                                    "jaca.model_request.total_tokens": getattr(
                                                        usage, "total_tokens", 0
                                                    )
                                                    if usage
                                                    else 0,
                                                },
                                            )
                                        else:
                                            _set_span_attributes(
                                                model_request_span,
                                                {"jaca.model_request.status": "succeeded"},
                                            )
                                    except Exception as error:
                                        _set_span_attributes(
                                            model_request_span,
                                            {
                                                "jaca.model_request.status": "failed",
                                                "jaca.model_request.error_type": type(error).__name__,
                                                "jaca.model_request.error_message": str(error),
                                            },
                                        )
                                        raise
                                node = await agent_run.next(node)
                            elif isinstance(node, CallToolsNode):
                                steer_boundary_active = False
                                steer_boundary_submitted = False
                                if (
                                    steer_enabled
                                    and _call_tools_node_has_tool_calls(node)
                                ):
                                    steer_boundary_active = True
                                    assert activate_steer_boundary is not None
                                    await activate_steer_boundary(
                                        lambda prompts: setattr(
                                            node, "user_prompt", prompts
                                        )
                                    )
    
                                async def _submit_if_ready() -> None:
                                    nonlocal steer_boundary_submitted
                                    if (
                                        steer_boundary_active
                                        and not steer_boundary_submitted
                                        and not pending_tool_calls
                                    ):
                                        assert submit_steer_boundary is not None
                                        await submit_steer_boundary()
                                        steer_boundary_submitted = True
    
                                try:
                                    async with node.stream(agent_run.ctx) as stream:
                                        stream_iterator = stream.__aiter__()
                                        stream_task = asyncio.create_task(
                                            anext(stream_iterator)
                                        )
                                        update_task = asyncio.create_task(queue.get())
                                        try:
                                            while True:
                                                done, _pending = await asyncio.wait(
                                                    {stream_task, update_task},
                                                    return_when=asyncio.FIRST_COMPLETED,
                                                )
    
                                                if update_task in done:
                                                    event = update_task.result()
                                                    pending_tool_call = (
                                                        _resolve_tool_update(
                                                            pending_tool_calls=pending_tool_calls,
                                                            buffered_tool_updates=buffered_tool_updates,
                                                            completed_tool_calls=completed_tool_calls,
                                                            queued_update=event,
                                                        )
                                                    )
                                                    if pending_tool_call is not None:
                                                        yield _build_tool_updated_event(
                                                            run_id=run_id,
                                                            queued_update=event,
                                                            pending_tool_call=pending_tool_call,
                                                        )
                                                    update_task = asyncio.create_task(
                                                        queue.get()
                                                    )
    
                                                if stream_task in done:
                                                    try:
                                                        event = stream_task.result()
                                                    except StopAsyncIteration:
                                                        break
    
                                                    saw_streamed_event = True
                                                    if isinstance(
                                                        event, FunctionToolCallEvent
                                                    ):
                                                        try:
                                                            tcr = _process_tool_call_event(
                                                                event=event,
                                                                run_id=run_id,
                                                                session_id=session_id,
                                                                pending_tool_calls=pending_tool_calls,
                                                                buffered_tool_updates=buffered_tool_updates,
                                                                completed_tool_calls=completed_tool_calls,
                                                                active_tool_spans=active_tool_spans,
                                                                available_tool_names=available_tool_names,
                                                                correction_attempts=correction_attempts,
                                                                run_span=run_span,
                                                            )
                                                        except _RestartRunWithCorrection as exc:
                                                            for ev in exc.pre_raise_events:
                                                                yield ev
                                                            raise
                                                        for ev in tcr.events:
                                                            yield ev
                                                        if tcr.terminal:
                                                            return
                                                        if tcr.is_duplicate:
                                                            stream_task = (
                                                                asyncio.create_task(
                                                                    anext(stream_iterator)
                                                                )
                                                            )
                                                            continue
                                                        stream_task = asyncio.create_task(
                                                            anext(stream_iterator)
                                                        )
                                                        continue

                                                    if isinstance(
                                                        event, FunctionToolResultEvent
                                                    ):
                                                        try:
                                                            result_events = _process_tool_result_event(
                                                                event=event,
                                                                run_id=run_id,
                                                                pending_tool_calls=pending_tool_calls,
                                                                completed_tool_calls=completed_tool_calls,
                                                                active_tool_spans=active_tool_spans,
                                                            )
                                                        except _RestartRunWithCorrection as exc:
                                                            for ev in exc.pre_raise_events:
                                                                yield ev
                                                            await _submit_if_ready()
                                                            raise
                                                        for ev in result_events:
                                                            yield ev
                                                        await _submit_if_ready()
                                                    stream_task = asyncio.create_task(
                                                        anext(stream_iterator)
                                                    )
                                        finally:
                                            for task in (stream_task, update_task):
                                                if not task.done():
                                                    task.cancel()
                                                    with contextlib.suppress(
                                                        asyncio.CancelledError
                                                    ):
                                                        await task
                                finally:
                                    if steer_boundary_active:
                                        assert deactivate_steer_boundary is not None
                                        await deactivate_steer_boundary()
    
                                node = await agent_run.next(node)
                            else:
                                node = await agent_run.next(node)
    
                            if isinstance(node, End):
                                result = agent_run.result
                                if result is None:
                                    raise RuntimeError(
                                        "PydanticAI stream ended without a terminal result"
                                    )
                                output = result.output
                                if not isinstance(output, str):
                                    output_type = type(output).__name__
                                    raise TypeError(
                                        "stream_run_events requires text output, got "
                                        f"{output_type}"
                                    )
                                terminal_messages = [
                                    *carried_messages,
                                    *result.new_messages(),
                                ]
                                if pending_tool_calls:
                                    if message_history_sink is not None:
                                        message_history_sink(terminal_messages)
                                    unresolved_message = (
                                        "Run cannot terminate with unresolved tool calls"
                                    )
                                    for event in synthesize_tool_failed_events_for_pending(
                                        run_id=run_id,
                                        pending=(
                                            PendingToolCall(
                                                tool_call_id=tool_call_id,
                                                tool_name=pending_tool_call.tool_name,
                                                args=pending_tool_call.args,
                                                args_valid=pending_tool_call.args_valid,
                                                started_at=pending_tool_call.started_at,
                                            )
                                            for tool_call_id, pending_tool_call in (
                                                pending_tool_calls.items()
                                            )
                                        ),
                                        error_type="SessionFormatError",
                                        message=unresolved_message,
                                    ):
                                        yield event
                                    _finish_all_tool_spans(
                                        active_tool_spans=active_tool_spans,
                                        status="failed",
                                        error_type="SessionFormatError",
                                        error_message=unresolved_message,
                                    )
                                    pending_tool_calls.clear()
                                    completed_tool_calls.clear()
                                    terminal_emitted = True
                                    _set_span_attributes(
                                        run_span,
                                        {
                                            "jaca.run.status": "failed",
                                            "jaca.run.error_type": "SessionFormatError",
                                            "jaca.run.error_message": unresolved_message,
                                        },
                                    )
                                    yield RunFailedEvent(
                                        run_id=run_id,
                                        error_type="SessionFormatError",
                                        message=unresolved_message,
                                    )
                                    return
                                terminal_emitted = True
                                if message_history_sink is not None:
                                    message_history_sink(terminal_messages)
                                _raise_if_buffered_tool_updates_remain(
                                    buffered_tool_updates
                                )
                                _set_span_attributes(
                                    run_span,
                                    {"jaca.run.status": "succeeded"},
                                )
                                yield _build_run_succeeded_event(
                                    run_id=run_id,
                                    agent=agent,
                                    output=output,
                                    usage=result.usage(),
                                )
                                return
            except Exception as error:
                if terminal_emitted:
                    raise RuntimeError(
                        "stream_run_events received an error after terminal success"
                    ) from error
    
                if isinstance(error, _InRunCompactRequested):
                    live_messages = [
                        *current_message_history,
                        *list(captured_messages)[attempt_history_count:],
                    ]
                    outcome = _attempt_in_run_compaction(
                        run_id=run_id,
                        live_messages=live_messages,
                        synthetic_prompt_counts=synthetic_prompt_counts,
                        agent=agent,
                        deps=deps,
                        thinking=thinking,
                        in_run_compact_failures=in_run_compact_failures,
                        active_tool_spans=active_tool_spans,
                    )
                    if not outcome.succeeded:
                        in_run_compact_failures += 1
                        compaction_restarting = True
                        continue
                    assert outcome.new_message_history is not None
                    assert outcome.new_synthetic_prompt_counts is not None
                    assert outcome.new_carried_messages is not None
                    assert outcome.new_prompt is not None
                    assert outcome.event is not None
                    current_message_history = outcome.new_message_history
                    synthetic_prompt_counts = outcome.new_synthetic_prompt_counts
                    carried_messages[:] = outcome.new_carried_messages
                    current_prompt = outcome.new_prompt
                    pending_tool_calls.clear()
                    completed_tool_calls.clear()
                    recovery_attempts = 0
                    last_response_usage = None
                    yield outcome.event
                    compaction_restarting = True
                    continue
    
                if isinstance(error, _RestartRunWithCorrection):
                    attempt_messages = _fallback_attempt_messages(
                        list(captured_messages)[attempt_history_count:],
                        prompt=current_prompt,
                    )
                    carried_messages.extend(sanitize_failed_run_messages(attempt_messages))
                    current_message_history = [
                        *current_message_history,
                        *carried_messages[len(current_message_history) :],
                    ]
                    current_prompt = error.message
                    synthetic_prompt_counts[error.message] = (
                        synthetic_prompt_counts.get(error.message, 0) + 1
                    )
                    correction_attempts += 1
                    _finish_all_tool_spans(
                        active_tool_spans=active_tool_spans,
                        status="aborted",
                        error_type=type(error).__name__,
                        error_message=error.message,
                    )
                    pending_tool_calls.clear()
                    completed_tool_calls.clear()
                    recovery_attempts = 0
                    continue
                if should_retry_run_error(
                    error=error,
                    saw_streamed_event=saw_streamed_event,
                    attempts=recovery_attempts,
                ):
                    logger.debug(
                        "Retrying transient pre-stream run failure: run_id=%s attempt=%s "
                        "error_type=%s message=%s",
                        run_id,
                        recovery_attempts + 1,
                        type(error).__name__,
                        str(error),
                    )
                    recovery_attempts += 1
                    continue
    
                for event in synthesize_tool_failed_events_for_pending(
                    run_id=run_id,
                    pending=(
                        PendingToolCall(
                            tool_call_id=tool_call_id,
                            tool_name=pending_tool_call.tool_name,
                            args=pending_tool_call.args,
                            args_valid=pending_tool_call.args_valid,
                            started_at=pending_tool_call.started_at,
                        )
                        for tool_call_id, pending_tool_call in pending_tool_calls.items()
                    ),
                    error_type=type(error).__name__,
                    message=str(error),
                ):
                    yield event
    
                _finish_all_tool_spans(
                    active_tool_spans=active_tool_spans,
                    status="failed",
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                terminal_emitted = True
                _set_span_attributes(
                    run_span,
                    {
                        "jaca.run.status": "failed",
                        "jaca.run.error_type": type(error).__name__,
                        "jaca.run.error_message": str(error),
                    },
                )
                if message_history_sink is not None:
                    message_history_sink(
                        [
                            *carried_messages,
                            *list(captured_messages)[attempt_history_count:],
                        ]
                    )
                yield RunFailedEvent(
                    run_id=run_id,
                    error_type=type(error).__name__,
                    message=str(error),
                )
                return
            finally:
                if (
                    not terminal_emitted
                    and not compaction_restarting
                    and message_history_sink is not None
                ):
                    # Cancellation (or any other BaseException) propagates through
                    # the body without hitting the `except Exception` branch above,
                    # so publish whatever pydantic-ai accumulated before the abort.
                    # The sink must be fired here so session.py can trust that
                    # authoritative_messages is populated for every terminal path.
                    message_history_sink(
                        [
                            *carried_messages,
                            *list(captured_messages)[attempt_history_count:],
                        ]
                    )
                if not terminal_emitted and not compaction_restarting:
                    _set_span_attributes(
                        run_span,
                        {
                            "jaca.run.status": "aborted",
                            "jaca.run.error_type": "CancelledError",
                            "jaca.run.error_message": "Run terminated before a terminal event",
                        },
                    )
                _finish_all_tool_spans(
                    active_tool_spans=active_tool_spans,
                    status="aborted",
                    error_type="CancelledError",
                    error_message="Run terminated before a terminal event",
                )
            if isinstance(queued_deps, WorkspaceDeps):
                await queued_deps.read_only_worker.close()



async def stream_run_events(
    *,
    agent: Agent[Any, Any],
    prompt: str,
    message_history: Sequence[ModelMessage] | None = None,
    instructions: str | None = None,
    thinking: ThinkingSetting | None = None,
    deps: WorkspaceDeps | None = None,
    message_history_sink: Callable[[Sequence[ModelMessage]], None] | None = None,
    available_tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
    activate_steer_boundary: (
        Callable[[Callable[[list[str]], None]], Awaitable[None]]
    ) | None = None,
    submit_steer_boundary: Callable[[], Awaitable[None]] | None = None,
    deactivate_steer_boundary: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[RunEvent]:
    """Translate one PydanticAI run into the canonical streamed event contract."""
    steer_callbacks_present = (
        activate_steer_boundary is not None,
        submit_steer_boundary is not None,
        deactivate_steer_boundary is not None,
    )
    if any(steer_callbacks_present) and not all(steer_callbacks_present):
        raise ValueError(
            "activate_steer_boundary, submit_steer_boundary, and "
            "deactivate_steer_boundary must be provided together"
        )

    run_id = uuid4().hex
    run_started_at = monotonic()
    event_history: list[RunEvent] = []

    async for event in _stream_run_events(
        agent=agent,
        run_id=run_id,
        prompt=prompt,
        message_history=message_history,
        instructions=instructions,
        thinking=thinking,
        deps=deps,
        message_history_sink=message_history_sink,
        available_tool_names=available_tool_names,
        activate_steer_boundary=activate_steer_boundary,
        submit_steer_boundary=submit_steer_boundary,
        deactivate_steer_boundary=deactivate_steer_boundary,
    ):
        if isinstance(event, RunSucceededEvent):
            event = event.model_copy(
                update={
                    "transcript_summary": build_run_transcript_summary(
                        events=event_history,
                        terminal_event=event,
                        elapsed_ms=_duration_ms_since(run_started_at),
                    )
                }
            )
        event_history.append(event)
        yield event


def _extract_text_delta(event: object) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content or None

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta or None

    return None


def _resolve_pending_tool_call(
    *,
    pending_tool_calls: dict[str, _PendingToolCall],
    tool_call_id: str,
    result_tool_name: str | None,
) -> _PendingToolCall:
    pending_tool_call = pending_tool_calls.get(tool_call_id)
    if pending_tool_call is None:
        raise RuntimeError(
            f"Tool result must match a pending tool_call_started: {tool_call_id}"
        )

    if result_tool_name is not None and result_tool_name != pending_tool_call.tool_name:
        raise RuntimeError(
            "Tool result tool_name mismatch for tool_call_id "
            f"{tool_call_id!r}: expected {pending_tool_call.tool_name!r}, got "
            f"{result_tool_name!r}"
        )

    pending_tool_calls.pop(tool_call_id)
    return pending_tool_call


def _resolve_tool_update(
    *,
    pending_tool_calls: dict[str, _PendingToolCall],
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]],
    completed_tool_calls: dict[str, str],
    queued_update: _QueuedToolUpdate,
) -> _PendingToolCall | None:
    pending_tool_call = pending_tool_calls.get(queued_update.tool_call_id)
    if pending_tool_call is None:
        completed_tool_name = completed_tool_calls.get(queued_update.tool_call_id)
        if completed_tool_name is not None:
            if queued_update.tool_name != completed_tool_name:
                raise RuntimeError(
                    "Tool update tool_name mismatch for completed tool_call_id "
                    f"{queued_update.tool_call_id!r}: expected "
                    f"{completed_tool_name!r}, got {queued_update.tool_name!r}"
                )
            return None
        buffered_tool_updates.setdefault(queued_update.tool_call_id, []).append(
            queued_update
        )
        return None

    if queued_update.tool_name != pending_tool_call.tool_name:
        raise RuntimeError(
            "Tool update tool_name mismatch for tool_call_id "
            f"{queued_update.tool_call_id!r}: expected "
            f"{pending_tool_call.tool_name!r}, got {queued_update.tool_name!r}"
        )

    return pending_tool_call


def _normalize_tool_args(
    value: str | dict[str, Any] | None,
    *,
    args_valid: bool | None,
) -> _NormalizedToolArgs:
    if value is None:
        return _NormalizedToolArgs(args=None, args_valid=args_valid)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            # Canonical event translation requires JSON-normalizable tool args.
            return _NormalizedToolArgs(args=None, args_valid=False)

        return _NormalizedToolArgs(
            args=_normalize_json_value(parsed),
            args_valid=args_valid,
        )

    return _NormalizedToolArgs(
        args=_normalize_json_value(value),
        args_valid=args_valid,
    )


def _normalize_json_value(value: Any) -> JsonValue | None:
    return _JSON_VALUE_ADAPTER.validate_python(value)


def _retry_prompt_message(part: RetryPromptPart) -> str:
    if isinstance(part.content, str):
        return part.content

    return part.model_response()


def _duration_ms_since(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))


def _get_context_window_tokens(agent: Agent[Any, Any]) -> int | None:
    model = getattr(agent, "model", None)
    if model is None:
        return None
    return get_model_context_window_tokens(model)


def _tool_error_result(*, error_type: str, message: str) -> dict[str, str | bool]:
    return {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }


def _tool_correction_exhausted_message(tool_name: str) -> str:
    return (
        f"Tool {tool_name!r} exceeded max retries count of "
        f"{CANONICAL_AGENT_TOOL_CORRECTION_RETRIES}"
    )


def _fallback_attempt_messages(
    messages: Sequence[ModelMessage],
    *,
    prompt: str,
) -> list[ModelMessage]:
    if messages:
        return list(messages)
    if prompt == "":
        return []
    return [ModelRequest(parts=[UserPromptPart(content=prompt)])]


def _malformed_tool_correction_message(
    *,
    tool_name: str,
    raw_args: str | dict[str, Any] | None,
    args_valid: bool | None,
    available_tool_names: Sequence[str],
) -> str | None:
    if tool_name not in available_tool_names:
        available_tools = ", ".join(repr(name) for name in available_tool_names)
        return f"Unknown tool name: {tool_name!r}. Available tools: {available_tools}"
    if args_valid is not False:
        return None
    if isinstance(raw_args, str):
        try:
            json.loads(raw_args)
        except json.JSONDecodeError as error:
            return (
                f"Invalid JSON for tool {tool_name!r}: {error.msg} at line "
                f"{error.lineno} column {error.colno}. Fix the errors and try again."
            )
    return (
        f"Invalid arguments for tool {tool_name!r}. "
        "Fix the errors and try again."
    )
