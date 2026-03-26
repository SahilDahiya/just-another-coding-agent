from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from pi_code_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
)
from pi_code_agent.runtime.run import stream_run_events


async def hello_stream(_messages: object, _agent_info: object) -> AsyncIterator[str]:
    yield "hello "
    yield "world"


async def broken_stream(_messages: object, _agent_info: object) -> AsyncIterator[str]:
    raise RuntimeError("boom")
    if False:  # pragma: no cover
        yield ""


async def looping_tool_stream(
    messages: object,
    _agent_info: object,
) -> AsyncIterator[dict[int, DeltaToolCall]]:
    tool_call_id = f"call-{len(messages)}"
    yield {
        0: DeltaToolCall(
            name="tick",
            json_args="{}",
            tool_call_id=tool_call_id,
        )
    }


async def test_stream_run_events_success() -> None:
    agent = Agent(FunctionModel(stream_function=hello_stream), output_type=str)

    events = [event async for event in stream_run_events(agent=agent, prompt="say hi")]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], AssistantTextDeltaEvent)
    assert isinstance(events[2], AssistantTextDeltaEvent)
    assert isinstance(events[3], RunSucceededEvent)

    run_id = events[0].run_id
    assert run_id
    assert [event.run_id for event in events] == [run_id, run_id, run_id, run_id]
    assert [events[1].delta, events[2].delta] == ["hello ", "world"]
    assert events[3].output_text == "hello world"


async def test_stream_run_events_failure_is_terminal_error_event() -> None:
    agent = Agent(FunctionModel(stream_function=broken_stream), output_type=str)

    events = [event async for event in stream_run_events(agent=agent, prompt="say hi")]

    assert len(events) == 2
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], RunFailedEvent)
    assert events[1].run_id == events[0].run_id
    assert events[1].error_type == "RuntimeError"
    assert events[1].message == "boom"


async def test_stream_run_events_usage_limit_failure_is_terminal_error_event() -> None:
    agent = Agent(FunctionModel(stream_function=looping_tool_stream), output_type=str)

    @agent.tool_plain
    async def tick() -> str:
        return "ok"

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            usage_limits=UsageLimits(request_limit=1),
        )
    ]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert events[1].type == "tool_call_started"
    assert events[2].type == "tool_call_succeeded"
    assert isinstance(events[3], RunFailedEvent)
    assert events[3].error_type == "UsageLimitExceeded"
    assert events[3].message == "The next request would exceed the request_limit of 1"
