from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
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
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.agent import build_canonical_model_settings
from just_another_coding_agent.runtime.recovery import should_retry_run_error

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
logger = logging.getLogger(__name__)


async def stream_run_events(
    *,
    agent: Agent[Any, Any],
    prompt: str,
    message_history: Sequence[ModelMessage] | None = None,
    thinking: ThinkingSetting | None = None,
) -> AsyncIterator[RunEvent]:
    """Translate one PydanticAI run into the canonical streamed event contract.

    Runtime exceptions before a terminal run event are converted into canonical
    failure events by design. Any exception after terminal success is invalid
    state and is raised.
    """
    run_id = uuid4().hex
    recovery_attempts = 0

    yield RunStartedEvent(run_id=run_id)

    while True:
        pending_tool_calls: dict[str, str] = {}
        saw_streamed_event = False
        terminal_emitted = False

        try:
            async for event in agent.run_stream_events(
                prompt,
                message_history=message_history,
                model_settings=build_canonical_model_settings(thinking=thinking),
            ):
                saw_streamed_event = True
                if isinstance(event, FunctionToolCallEvent):
                    args = _normalize_tool_args(event.part.args)
                    pending_tool_calls[event.tool_call_id] = event.part.tool_name
                    yield ToolCallStartedEvent(
                        run_id=run_id,
                        tool_call_id=event.tool_call_id,
                        tool_name=event.part.tool_name,
                        args=args,
                        args_valid=event.args_valid,
                    )
                    continue

                if isinstance(event, FunctionToolResultEvent):
                    if isinstance(event.result, RetryPromptPart):
                        tool_name = _resolve_pending_tool_name(
                            pending_tool_calls=pending_tool_calls,
                            tool_call_id=event.tool_call_id,
                            result_tool_name=event.result.tool_name,
                        )
                        yield ToolCallFailedEvent(
                            run_id=run_id,
                            tool_call_id=event.tool_call_id,
                            tool_name=tool_name,
                            error_type="RetryPromptPart",
                            message=_retry_prompt_message(event.result),
                        )
                        continue

                    tool_name = _resolve_pending_tool_name(
                        pending_tool_calls=pending_tool_calls,
                        tool_call_id=event.tool_call_id,
                        result_tool_name=event.result.tool_name,
                    )
                    result = _normalize_json_value(event.result.content)
                    yield ToolCallSucceededEvent(
                        run_id=run_id,
                        tool_call_id=event.tool_call_id,
                        tool_name=tool_name,
                        result=result,
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
                            f"stream_run_events requires text output, got {output_type}"
                        )

                    terminal_emitted = True
                    yield RunSucceededEvent(run_id=run_id, output_text=output)

            if not terminal_emitted:
                raise RuntimeError("PydanticAI stream ended without a terminal result")
            return
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

            for tool_call_id, tool_name in pending_tool_calls.items():
                yield ToolCallFailedEvent(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_type=type(error).__name__,
                    message=str(error),
                )

            yield RunFailedEvent(
                run_id=run_id,
                error_type=type(error).__name__,
                message=str(error),
            )
            return


def _extract_text_delta(event: object) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content or None

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta or None

    return None


def _resolve_pending_tool_name(
    *,
    pending_tool_calls: dict[str, str],
    tool_call_id: str,
    result_tool_name: str | None,
) -> str:
    pending_tool_name = pending_tool_calls.get(tool_call_id)
    if pending_tool_name is None:
        raise RuntimeError(
            f"Tool result must match a pending tool_call_started: {tool_call_id}"
        )

    if result_tool_name is not None and result_tool_name != pending_tool_name:
        raise RuntimeError(
            "Tool result tool_name mismatch for tool_call_id "
            f"{tool_call_id!r}: expected {pending_tool_name!r}, got "
            f"{result_tool_name!r}"
        )

    pending_tool_calls.pop(tool_call_id)
    return pending_tool_name


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
