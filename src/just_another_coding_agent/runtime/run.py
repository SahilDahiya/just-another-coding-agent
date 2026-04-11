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
    AgentRunResultEvent,
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
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_graph import End

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
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import (
    build_failed_tool_activity,
    build_started_tool_activity,
    build_succeeded_tool_activity,
    build_updated_tool_activity,
)
from just_another_coding_agent.runtime.agent import (
    CANONICAL_AGENT_TOOL_CORRECTION_RETRIES,
)
from just_another_coding_agent.runtime.models import (
    build_canonical_model_settings,
    get_model_context_window_tokens,
)
from just_another_coding_agent.runtime.recovery import should_retry_run_error
from just_another_coding_agent.runtime.transcript_summary import (
    build_run_transcript_summary,
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


@dataclass(frozen=True)
class _QueuedRunError:
    error: Exception


@dataclass(frozen=True)
class _QueuedRunFinished:
    pass


@dataclass(frozen=True)
class _RestartRunWithCorrection(Exception):
    message: str


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


async def _stream_run_events_with_steer(
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
    activate_steer_boundary: Callable[[Callable[[list[str]], None]], Awaitable[None]],
    submit_steer_boundary: Callable[[], Awaitable[None]],
    deactivate_steer_boundary: Callable[[], Awaitable[None]],
) -> AsyncIterator[RunEvent]:
    recovery_attempts = 0
    correction_attempts = 0
    current_prompt = prompt
    current_message_history = list(message_history or [])
    carried_messages: list[ModelMessage] = []
    pending_tool_calls: dict[str, _PendingToolCall] = {}
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]] = {}
    completed_tool_calls: dict[str, str] = {}
    yield RunStartedEvent(run_id=run_id)

    while True:
        saw_streamed_event = False
        terminal_emitted = False
        attempt_history_count = len(current_message_history)
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
            with capture_run_messages() as captured_messages:
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
                            async with node.stream(agent_run.ctx) as stream:
                                async for event in stream:
                                    saw_streamed_event = True
                                    text_delta = _extract_text_delta(event)
                                    if text_delta is not None:
                                        yield AssistantTextDeltaEvent(
                                            run_id=run_id,
                                            delta=text_delta,
                                        )
                            node = await agent_run.next(node)
                        elif isinstance(node, CallToolsNode):
                            steer_boundary_active = False
                            steer_boundary_submitted = False
                            if _call_tools_node_has_tool_calls(node):
                                steer_boundary_active = True
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
                                                    normalized_args = (
                                                        _normalize_tool_args(
                                                        event.part.args,
                                                        args_valid=event.args_valid,
                                                        )
                                                    )
                                                    args = normalized_args.args
                                                    args_valid = (
                                                        normalized_args.args_valid
                                                    )
                                                    existing_tool_call = (
                                                        pending_tool_calls.get(
                                                            event.tool_call_id
                                                        )
                                                    )
                                                    if existing_tool_call is not None:
                                                        if (
                                                            existing_tool_call.tool_name
                                                            != event.part.tool_name
                                                            or existing_tool_call.args
                                                            != args
                                                        ):
                                                            raise RuntimeError(
                                                                "Duplicate tool call "
                                                                "mismatch for "
                                                                "tool_call_id "
                                                                f"{event.tool_call_id!r}"
                                                            )
                                                        stream_task = (
                                                            asyncio.create_task(
                                                                anext(
                                                                    stream_iterator
                                                                )
                                                            )
                                                        )
                                                        continue
                                                    pending_tool_calls[
                                                        event.tool_call_id
                                                    ] = _PendingToolCall(
                                                        tool_name=event.part.tool_name,
                                                        args=args,
                                                        args_valid=args_valid,
                                                        started_at=monotonic(),
                                                    )
                                                    yield ToolCallStartedEvent(
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
                                                    )
                                                    updates = (
                                                        _drain_buffered_tool_updates(
                                                            run_id=run_id,
                                                            pending_tool_call=(
                                                                pending_tool_calls[
                                                                    event.tool_call_id
                                                                ]
                                                            ),
                                                            tool_call_id=event.tool_call_id,
                                                            buffered_tool_updates=buffered_tool_updates,
                                                        )
                                                    )
                                                    for update in updates:
                                                        yield update
                                                    malformed_message = (
                                                        _malformed_tool_correction_message(
                                                            tool_name=event.part.tool_name,
                                                            raw_args=event.part.args,
                                                            args_valid=args_valid,
                                                            available_tool_names=available_tool_names,
                                                        )
                                                    )
                                                    if malformed_message is not None:
                                                        pending_tool_call = (
                                                            pending_tool_calls.pop(
                                                                event.tool_call_id
                                                            )
                                                        )
                                                        completed_tool_calls[
                                                            event.tool_call_id
                                                        ] = pending_tool_call.tool_name
                                                        if correction_attempts >= (
                                                            CANONICAL_AGENT_TOOL_CORRECTION_RETRIES
                                                        ):
                                                            exhausted_message = (
                                                                _tool_correction_exhausted_message(
                                                                    pending_tool_call.tool_name
                                                                )
                                                            )
                                                            yield ToolCallFailedEvent(
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
                                                            yield RunFailedEvent(
                                                                run_id=run_id,
                                                                error_type="RuntimeError",
                                                                message=exhausted_message,
                                                            )
                                                            return
                                                        retry_result = (
                                                            _tool_error_result(
                                                            error_type="RetryPromptPart",
                                                            message=malformed_message,
                                                            )
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
                                                        raise _RestartRunWithCorrection(
                                                            malformed_message
                                                        )
                                                    stream_task = asyncio.create_task(
                                                        anext(stream_iterator)
                                                    )
                                                    continue

                                                if isinstance(
                                                    event, FunctionToolResultEvent
                                                ):
                                                    if isinstance(
                                                        event.result, RetryPromptPart
                                                    ):
                                                        # Defensive only:
                                                        # malformed tool correction
                                                        # is runtime-owned now, but
                                                        # keep honoring an unexpected
                                                        # RetryPromptPart if one
                                                        # still surfaces from the
                                                        # framework.
                                                        pending_tool_call = (
                                                            _resolve_pending_tool_call(
                                                                pending_tool_calls=(
                                                                    pending_tool_calls
                                                                ),
                                                                tool_call_id=event.tool_call_id,
                                                                result_tool_name=(
                                                                    event.result.tool_name
                                                                ),
                                                            )
                                                        )
                                                        completed_tool_calls[
                                                            event.tool_call_id
                                                        ] = pending_tool_call.tool_name
                                                        retry_message = (
                                                            _retry_prompt_message(
                                                                event.result
                                                            )
                                                        )
                                                        retry_result = (
                                                            _tool_error_result(
                                                            error_type="RetryPromptPart",
                                                            message=retry_message,
                                                            )
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
                                                        await _submit_if_ready()
                                                        raise _RestartRunWithCorrection(
                                                            retry_message
                                                        )

                                                    pending_tool_call = (
                                                        _resolve_pending_tool_call(
                                                            pending_tool_calls=(
                                                                pending_tool_calls
                                                            ),
                                                            tool_call_id=event.tool_call_id,
                                                            result_tool_name=(
                                                                event.result.tool_name
                                                            ),
                                                        )
                                                    )
                                                    completed_tool_calls[
                                                        event.tool_call_id
                                                    ] = pending_tool_call.tool_name
                                                    result = _normalize_json_value(
                                                        event.result.content
                                                    )
                                                    result_metadata = getattr(
                                                        event.result, "metadata", None
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
                                if _call_tools_node_has_tool_calls(node):
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
                            terminal_emitted = True
                            if message_history_sink is not None:
                                message_history_sink(
                                    [*carried_messages, *result.new_messages()]
                                )
                            _raise_if_buffered_tool_updates_remain(
                                buffered_tool_updates
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

            if isinstance(error, _RestartRunWithCorrection):
                attempt_messages = _fallback_attempt_messages(
                    list(captured_messages)[attempt_history_count:],
                    prompt=current_prompt,
                )
                carried_messages.extend(_sanitize_failed_run_messages(attempt_messages))
                current_message_history = [
                    *current_message_history,
                    *carried_messages[len(current_message_history) :],
                ]
                current_prompt = error.message
                correction_attempts += 1
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

            for tool_call_id, pending_tool_call in pending_tool_calls.items():
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
                        duration_ms=_duration_ms_since(pending_tool_call.started_at),
                    ),
                )

            terminal_emitted = True
            if message_history_sink is not None:
                try:
                    message_history_sink(
                        [
                            *carried_messages,
                            *list(captured_messages)[attempt_history_count:],
                        ]
                    )
                except Exception:
                    pass
            yield RunFailedEvent(
                run_id=run_id,
                error_type=type(error).__name__,
                message=str(error),
            )
            return
        finally:
            if (
                not terminal_emitted
                and message_history_sink is not None
            ):
                # Cancellation (or any other BaseException) propagates through
                # the body without hitting the `except Exception` branch above,
                # so publish whatever pydantic-ai accumulated before the abort
                # so session.py can persist partial message history without
                # needing its own capture_run_messages fallback.
                try:
                    message_history_sink(
                        [
                            *carried_messages,
                            *list(captured_messages)[attempt_history_count:],
                        ]
                    )
                except Exception:
                    pass
            if isinstance(queued_deps, WorkspaceDeps):
                await queued_deps.read_only_worker.close()


async def _stream_run_events_inner(
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
    """Translate one PydanticAI run into the canonical streamed event contract.

    Runtime exceptions before a terminal run event are converted into canonical
    failure events by design. Any exception after terminal success is invalid
    state and is raised.
    """
    run_id = uuid4().hex
    if (
        activate_steer_boundary is not None
        and submit_steer_boundary is not None
        and deactivate_steer_boundary is not None
    ):
        async for event in _stream_run_events_with_steer(
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
            yield event
        return

    recovery_attempts = 0
    correction_attempts = 0
    current_prompt = prompt
    current_message_history = list(message_history or [])
    carried_messages: list[ModelMessage] = []
    pending_tool_calls: dict[str, _PendingToolCall] = {}
    buffered_tool_updates: dict[str, list[_QueuedToolUpdate]] = {}
    completed_tool_calls: dict[str, str] = {}
    yield RunStartedEvent(run_id=run_id)

    while True:
        saw_streamed_event = False
        terminal_emitted = False
        attempt_history_count = len(current_message_history)
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
            queued_deps = _bind_workspace_deps_to_run(
                deps=deps,
                run_id=run_id,
                tool_update_sink=_queue_tool_update,
            )

        with capture_run_messages() as captured_messages:
            async def _pump_agent_events() -> None:
                try:
                    with agent.parallel_tool_call_execution_mode("parallel"):
                        async for event in agent.run_stream_events(
                            current_prompt,
                            message_history=(current_message_history or None),
                            instructions=instructions,
                            deps=queued_deps,
                            model_settings=build_canonical_model_settings(
                                model=getattr(agent, "model", None),
                                thinking=thinking,
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
                        if not terminal_emitted:
                            raise RuntimeError(
                                "PydanticAI stream ended without a terminal result"
                            )
                        _raise_if_buffered_tool_updates_remain(buffered_tool_updates)
                        return

                    if isinstance(event, _QueuedRunError):
                        raise event.error

                    if isinstance(event, _QueuedToolUpdate):
                        pending_tool_call = _resolve_tool_update(
                            pending_tool_calls=pending_tool_calls,
                            buffered_tool_updates=buffered_tool_updates,
                            completed_tool_calls=completed_tool_calls,
                            queued_update=event,
                        )
                        if pending_tool_call is None:
                            continue
                        yield _build_tool_updated_event(
                            run_id=run_id,
                            queued_update=event,
                            pending_tool_call=pending_tool_call,
                        )
                        continue

                    saw_streamed_event = True
                    if isinstance(event, FunctionToolCallEvent):
                        normalized_args = _normalize_tool_args(
                            event.part.args,
                            args_valid=event.args_valid,
                        )
                        args = normalized_args.args
                        args_valid = normalized_args.args_valid
                        existing_tool_call = pending_tool_calls.get(event.tool_call_id)
                        if existing_tool_call is not None:
                            if (
                                existing_tool_call.tool_name != event.part.tool_name
                                or existing_tool_call.args != args
                            ):
                                raise RuntimeError(
                                    "Duplicate tool call mismatch for tool_call_id "
                                    f"{event.tool_call_id!r}"
                                )
                            continue

                        pending_tool_calls[event.tool_call_id] = _PendingToolCall(
                            tool_name=event.part.tool_name,
                            args=args,
                            args_valid=args_valid,
                            started_at=monotonic(),
                        )
                        yield ToolCallStartedEvent(
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
                        )
                        updates = _drain_buffered_tool_updates(
                            run_id=run_id,
                            pending_tool_call=pending_tool_calls[event.tool_call_id],
                            tool_call_id=event.tool_call_id,
                            buffered_tool_updates=buffered_tool_updates,
                        )
                        for update in updates:
                            yield update
                        malformed_message = _malformed_tool_correction_message(
                            tool_name=event.part.tool_name,
                            raw_args=event.part.args,
                            args_valid=args_valid,
                            available_tool_names=available_tool_names,
                        )
                        if malformed_message is not None:
                            pending_tool_call = pending_tool_calls.pop(
                                event.tool_call_id
                            )
                            completed_tool_calls[event.tool_call_id] = (
                                pending_tool_call.tool_name
                            )
                            if (
                                correction_attempts
                                >= CANONICAL_AGENT_TOOL_CORRECTION_RETRIES
                            ):
                                exhausted_message = (
                                    _tool_correction_exhausted_message(
                                        pending_tool_call.tool_name
                                    )
                                )
                                yield ToolCallFailedEvent(
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
                                yield RunFailedEvent(
                                    run_id=run_id,
                                    error_type="RuntimeError",
                                    message=exhausted_message,
                                )
                                return
                            retry_result = _tool_error_result(
                                error_type="RetryPromptPart",
                                message=malformed_message,
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
                            raise _RestartRunWithCorrection(malformed_message)
                        continue

                    if isinstance(event, FunctionToolResultEvent):
                        if isinstance(event.result, RetryPromptPart):
                            # Defensive only: malformed tool correction is
                            # runtime-owned now, but keep honoring an
                            # unexpected RetryPromptPart if one still surfaces
                            # from the framework.
                            pending_tool_call = _resolve_pending_tool_call(
                                pending_tool_calls=pending_tool_calls,
                                tool_call_id=event.tool_call_id,
                                result_tool_name=event.result.tool_name,
                            )
                            completed_tool_calls[event.tool_call_id] = (
                                pending_tool_call.tool_name
                            )
                            retry_message = _retry_prompt_message(event.result)
                            retry_result = _tool_error_result(
                                error_type="RetryPromptPart",
                                message=retry_message,
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
                            raise _RestartRunWithCorrection(retry_message)

                        pending_tool_call = _resolve_pending_tool_call(
                            pending_tool_calls=pending_tool_calls,
                            tool_call_id=event.tool_call_id,
                            result_tool_name=event.result.tool_name,
                        )
                        completed_tool_calls[event.tool_call_id] = (
                            pending_tool_call.tool_name
                        )
                        result = _normalize_json_value(event.result.content)
                        result_metadata = getattr(event.result, "metadata", None)
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
                        if not isinstance(output, str):
                            output_type = type(output).__name__
                            raise TypeError(
                                "stream_run_events requires text output, got "
                                f"{output_type}"
                            )

                        terminal_emitted = True
                        if message_history_sink is not None:
                            message_history_sink(
                                [*carried_messages, *event.result.new_messages()]
                            )
                        usage = event.result.usage()
                        context_limit = _get_context_window_tokens(agent)
                        yield RunSucceededEvent(
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
            except Exception as error:
                if terminal_emitted:
                    raise RuntimeError(
                        "stream_run_events received an error after terminal success"
                    ) from error

                if isinstance(error, _RestartRunWithCorrection):
                    attempt_messages = _fallback_attempt_messages(
                        list(captured_messages)[attempt_history_count:],
                        prompt=current_prompt,
                    )
                    current_message_history.extend(
                        _sanitize_failed_run_messages(attempt_messages)
                    )
                    current_prompt = error.message
                    correction_attempts += 1
                    pending_tool_calls.clear()
                    completed_tool_calls.clear()
                    recovery_attempts = 0
                    continue

                if should_retry_run_error(
                    error=error,
                    saw_streamed_event=saw_streamed_event,
                    attempts=recovery_attempts,
                ):
                    # Keep one hidden retry at the canonical stream boundary so the
                    # public event contract never leaks duplicate run_started or
                    # partial inner-attempt events.
                    logger.debug(
                        "Retrying transient pre-stream run failure: "
                        "run_id=%s attempt=%s "
                        "error_type=%s message=%s",
                        run_id,
                        recovery_attempts + 1,
                        type(error).__name__,
                        str(error),
                    )
                    recovery_attempts += 1
                    continue

                for tool_call_id, pending_tool_call in pending_tool_calls.items():
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

                terminal_emitted = True
                if message_history_sink is not None:
                    try:
                        message_history_sink(
                            list(captured_messages)[attempt_history_count:]
                        )
                    except Exception:
                        pass
                yield RunFailedEvent(
                    run_id=run_id,
                    error_type=type(error).__name__,
                    message=str(error),
                )
                return
            finally:
                if (
                    not terminal_emitted
                    and message_history_sink is not None
                ):
                    # Cancellation (BaseException) propagates through the body
                    # without being caught above; publish whatever was
                    # accumulated so session.py can persist partial messages.
                    try:
                        message_history_sink(
                            list(captured_messages)[attempt_history_count:]
                        )
                    except Exception:
                        pass
                if not pump_task.done():
                    pump_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pump_task
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
    run_started_at = monotonic()
    event_history: list[RunEvent] = []

    async for event in _stream_run_events_inner(
        agent=agent,
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
                hasattr(part, "tool_call_id")
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
