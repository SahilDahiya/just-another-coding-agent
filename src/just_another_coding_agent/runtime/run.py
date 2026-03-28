from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
)
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import UsageLimits

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
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
from just_another_coding_agent.runtime.activity import (
    build_failed_tool_activity,
    build_started_tool_activity,
    build_succeeded_tool_activity,
    build_updated_tool_activity,
)
from just_another_coding_agent.runtime.deferred import execute_deferred_tool_requests
from just_another_coding_agent.runtime.models import build_canonical_model_settings
from just_another_coding_agent.runtime.recovery import should_retry_run_error
from just_another_coding_agent.runtime.tracing import RuntimeTraceRecorder
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
class _QueuedToolUpdate:
    tool_call_id: str
    tool_name: str
    partial_result: JsonValue | None


@dataclass(frozen=True)
class _QueuedRunError:
    error: Exception


@dataclass(frozen=True)
class _QueuedRunFinished:
    pass


@dataclass(frozen=True)
class _DeferredContinuation:
    message_history: Sequence[ModelMessage]
    deferred_tool_results: DeferredToolResults


def _build_unbounded_usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=None,
        tool_calls_limit=None,
        input_tokens_limit=None,
        output_tokens_limit=None,
        total_tokens_limit=None,
    )


async def stream_run_events(
    *,
    agent: Agent[Any, Any],
    prompt: str,
    message_history: Sequence[ModelMessage] | None = None,
    thinking: ThinkingSetting | None = None,
    deps: WorkspaceDeps | None = None,
    enable_server_history: bool = False,
    message_history_sink: Callable[[Sequence[ModelMessage]], None] | None = None,
) -> AsyncIterator[RunEvent]:
    """Translate one PydanticAI run into the canonical streamed event contract.

    Runtime exceptions before a terminal run event are converted into canonical
    failure events by design. Any exception after terminal success is invalid
    state and is raised.
    """
    run_id = uuid4().hex
    recovery_attempts = 0
    pending_tool_calls: dict[str, _PendingToolCall] = {}
    trace_recorder = RuntimeTraceRecorder(run_id=run_id)
    current_prompt: str | None = prompt
    current_message_history = message_history
    current_deferred_tool_results: DeferredToolResults | None = None

    yield RunStartedEvent(run_id=run_id)

    while True:
        saw_streamed_event = False
        terminal_emitted = False
        deferred_continuation: _DeferredContinuation | None = None
        queue: asyncio.Queue[object] = asyncio.Queue()

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

        queued_deps = deps
        if isinstance(deps, WorkspaceDeps):
            queued_deps = replace(
                deps,
                tool_update_sink=_queue_tool_update,
            )

        async def _pump_agent_events() -> None:
            try:
                with agent.parallel_tool_call_execution_mode("parallel"):
                    async for event in agent.run_stream_events(
                        current_prompt,
                        output_type=[str, DeferredToolRequests],
                        message_history=current_message_history,
                        deferred_tool_results=current_deferred_tool_results,
                        deps=queued_deps,
                        model_settings=build_canonical_model_settings(
                            model=getattr(agent, "model", None),
                            thinking=thinking,
                            enable_server_history=enable_server_history,
                        ),
                        usage_limits=_build_unbounded_usage_limits(),
                    ):
                        await queue.put(event)
            except Exception as error:
                await queue.put(_QueuedRunError(error))
            else:
                await queue.put(_QueuedRunFinished())

        pump_task = asyncio.create_task(_pump_agent_events())

        try:
            while True:
                event = await queue.get()
                if isinstance(event, _QueuedRunFinished):
                    if deferred_continuation is not None:
                        current_prompt = None
                        current_message_history = deferred_continuation.message_history
                        current_deferred_tool_results = (
                            deferred_continuation.deferred_tool_results
                        )
                        break
                    if not terminal_emitted:
                        raise RuntimeError(
                            "PydanticAI stream ended without a terminal result"
                        )
                    return

                if isinstance(event, _QueuedRunError):
                    raise event.error

                if isinstance(event, _QueuedToolUpdate):
                    pending_tool_call = _peek_pending_tool_call(
                        pending_tool_calls=pending_tool_calls,
                        tool_call_id=event.tool_call_id,
                        tool_name=event.tool_name,
                    )
                    yield ToolCallUpdatedEvent(
                        run_id=run_id,
                        tool_call_id=event.tool_call_id,
                        tool_name=pending_tool_call.tool_name,
                        partial_result=event.partial_result,
                        activity=build_updated_tool_activity(
                            tool_name=pending_tool_call.tool_name,
                            args=pending_tool_call.args,
                            args_valid=pending_tool_call.args_valid,
                            partial_result=event.partial_result,
                            duration_ms=_duration_ms_since(
                                pending_tool_call.started_at
                            ),
                        ),
                    )
                    continue

                saw_streamed_event = True
                if isinstance(event, FunctionToolCallEvent):
                    args = _normalize_tool_args(event.part.args)
                    existing_tool_call = pending_tool_calls.get(event.tool_call_id)
                    if existing_tool_call is not None:
                        if (
                            existing_tool_call.tool_name != event.part.tool_name
                            or existing_tool_call.args != args
                        ):
                            raise RuntimeError(
                                "Deferred tool restart mismatch for tool_call_id "
                                f"{event.tool_call_id!r}"
                            )
                        continue

                    pending_tool_calls[event.tool_call_id] = _PendingToolCall(
                        tool_name=event.part.tool_name,
                        args=args,
                        args_valid=event.args_valid,
                        started_at=monotonic(),
                    )
                    trace_recorder.start_tool(
                        tool_call_id=event.tool_call_id,
                        tool_name=event.part.tool_name,
                    )
                    yield ToolCallStartedEvent(
                        run_id=run_id,
                        tool_call_id=event.tool_call_id,
                        tool_name=event.part.tool_name,
                        args=args,
                        args_valid=event.args_valid,
                        activity=build_started_tool_activity(
                            tool_name=event.part.tool_name,
                            args=args,
                            args_valid=event.args_valid,
                        ),
                    )
                    continue

                if isinstance(event, FunctionToolResultEvent):
                    if isinstance(event.result, RetryPromptPart):
                        pending_tool_call = _resolve_pending_tool_call(
                            pending_tool_calls=pending_tool_calls,
                            tool_call_id=event.tool_call_id,
                            result_tool_name=event.result.tool_name,
                        )
                        retry_message = _retry_prompt_message(event.result)
                        retry_result = _tool_error_result(
                            error_type="RetryPromptPart",
                            message=retry_message,
                        )
                        trace_recorder.finish_tool(
                            tool_call_id=event.tool_call_id,
                            status="succeeded",
                        )
                        yield ToolCallSucceededEvent(
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
                        continue

                    pending_tool_call = _resolve_pending_tool_call(
                        pending_tool_calls=pending_tool_calls,
                        tool_call_id=event.tool_call_id,
                        result_tool_name=event.result.tool_name,
                    )
                    result = _normalize_json_value(event.result.content)
                    result_metadata = getattr(event.result, "metadata", None)
                    trace_recorder.finish_tool(
                        tool_call_id=event.tool_call_id,
                        status="succeeded",
                    )
                    yield ToolCallSucceededEvent(
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
                    continue

                text_delta = _extract_text_delta(event)
                if text_delta is not None:
                    yield AssistantTextDeltaEvent(run_id=run_id, delta=text_delta)
                    continue

                if isinstance(event, AgentRunResultEvent):
                    output = event.result.output
                    if isinstance(output, DeferredToolRequests):
                        if message_history_sink is not None:
                            message_history_sink(event.result.all_messages())
                        deferred_continuation = _DeferredContinuation(
                            message_history=event.result.all_messages(),
                            deferred_tool_results=(
                                await execute_deferred_tool_requests(
                                    requests=output,
                                    deps=queued_deps
                                    if isinstance(queued_deps, WorkspaceDeps)
                                    else None,
                                )
                            ),
                        )
                        continue
                    if not isinstance(output, str):
                        output_type = type(output).__name__
                        raise TypeError(
                            f"stream_run_events requires text output, got {output_type}"
                        )

                    terminal_emitted = True
                    if message_history_sink is not None:
                        message_history_sink(event.result.all_messages())
                    trace_recorder.finish_run(status="succeeded")
                    yield RunSucceededEvent(run_id=run_id, output_text=output)
        except Exception as error:
            if terminal_emitted:
                raise RuntimeError(
                    "stream_run_events received an error after terminal success"
                ) from error

            if should_retry_run_error(
                error=error,
                saw_streamed_event=saw_streamed_event,
                attempts=recovery_attempts,
            ):
                # Keep one hidden retry at the canonical stream boundary so the
                # public event contract never leaks duplicate run_started or
                # partial inner-attempt events.
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

            for tool_call_id, pending_tool_call in pending_tool_calls.items():
                trace_recorder.finish_tool(
                    tool_call_id=tool_call_id,
                    status="failed",
                )
                yield ToolCallFailedEvent(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool_name=pending_tool_call.tool_name,
                    error_type=type(error).__name__,
                    message=str(error),
                    activity=build_failed_tool_activity(
                        tool_name=pending_tool_call.tool_name,
                        args=pending_tool_call.args,
                        args_valid=pending_tool_call.args_valid,
                        message=str(error),
                        duration_ms=_duration_ms_since(
                            pending_tool_call.started_at
                        ),
                    ),
                )

            trace_recorder.finish_run(status="failed")
            yield RunFailedEvent(
                run_id=run_id,
                error_type=type(error).__name__,
                message=str(error),
            )
            return
        finally:
            if not pump_task.done():
                pump_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump_task


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

    if (
        result_tool_name is not None
        and result_tool_name != pending_tool_call.tool_name
    ):
        raise RuntimeError(
            "Tool result tool_name mismatch for tool_call_id "
            f"{tool_call_id!r}: expected {pending_tool_call.tool_name!r}, got "
            f"{result_tool_name!r}"
        )

    pending_tool_calls.pop(tool_call_id)
    return pending_tool_call


def _peek_pending_tool_call(
    *,
    pending_tool_calls: dict[str, _PendingToolCall],
    tool_call_id: str,
    tool_name: str,
) -> _PendingToolCall:
    pending_tool_call = pending_tool_calls.get(tool_call_id)
    if pending_tool_call is None:
        raise RuntimeError(
            f"Tool update must match a pending tool_call_started: {tool_call_id}"
        )

    if tool_name != pending_tool_call.tool_name:
        raise RuntimeError(
            "Tool update tool_name mismatch for tool_call_id "
            f"{tool_call_id!r}: expected {pending_tool_call.tool_name!r}, got "
            f"{tool_name!r}"
        )

    return pending_tool_call


def _normalize_tool_args(value: str | dict[str, Any] | None) -> JsonValue | None:
    if value is None:
        return None

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Tool args must be valid JSON") from error

        return _normalize_json_value(parsed)

    return _normalize_json_value(value)


def _normalize_json_value(value: Any) -> JsonValue | None:
    return _JSON_VALUE_ADAPTER.validate_python(value)


def _retry_prompt_message(part: RetryPromptPart) -> str:
    if isinstance(part.content, str):
        return part.content

    return part.model_response()


def _duration_ms_since(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))


def _tool_error_result(*, error_type: str, message: str) -> dict[str, str | bool]:
    return {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
