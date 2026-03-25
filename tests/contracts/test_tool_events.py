from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from pi_code_agent.runtime.run import stream_run_events


async def successful_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="add",
                json_args='{"a": 1, "b": 2}',
                tool_call_id="call-add",
            )
        }
        return

    yield "done"


async def failing_tool_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    yield {
        0: DeltaToolCall(
            name="explode",
            json_args="{}",
            tool_call_id="call-explode",
        )
    }


async def test_stream_run_events_tool_success() -> None:
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def add(a: int, b: int) -> int:
        return a + b

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 5
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert isinstance(events[3], AssistantTextDeltaEvent)
    assert isinstance(events[4], RunSucceededEvent)

    run_id = events[0].run_id
    assert [event.run_id for event in events] == [run_id] * 5
    assert events[1].tool_call_id == "call-add"
    assert events[1].tool_name == "add"
    assert events[1].args == {"a": 1, "b": 2}
    assert events[1].args_valid is True
    assert events[2].tool_call_id == "call-add"
    assert events[2].tool_name == "add"
    assert events[2].result == 3
    assert events[3].delta == "done"
    assert events[4].output_text == "done"


async def test_stream_run_events_tool_failure_is_terminal_error_event() -> None:
    agent = Agent(
        FunctionModel(stream_function=failing_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def explode() -> str:
        raise RuntimeError("tool boom")

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallFailedEvent)
    assert isinstance(events[3], RunFailedEvent)

    assert events[1].tool_call_id == "call-explode"
    assert events[1].tool_name == "explode"
    assert events[1].args == {}
    assert events[1].args_valid is True
    assert events[2].run_id == events[0].run_id
    assert events[2].tool_call_id == "call-explode"
    assert events[2].tool_name == "explode"
    assert events[2].error_type == "RuntimeError"
    assert events[2].message == "tool boom"
    assert events[3].error_type == "RuntimeError"
    assert events[3].message == "tool boom"
