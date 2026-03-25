from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from pydantic_ai import Agent, AgentRunResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import TextPart, TextPartDelta

from pi_code_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
)


async def stream_run_events(
    *,
    agent: Agent[Any, Any],
    prompt: str,
) -> AsyncIterator[RunEvent]:
    run_id = uuid4().hex
    terminal_emitted = False

    yield RunStartedEvent(run_id=run_id)

    try:
        async for event in agent.run_stream_events(prompt):
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
    except Exception as error:
        if terminal_emitted:
            raise RuntimeError(
                "stream_run_events received an error after terminal success"
            ) from error

        yield RunFailedEvent(
            run_id=run_id,
            error_type=type(error).__name__,
            message=str(error),
        )


def _extract_text_delta(event: object) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content or None

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta or None

    return None
