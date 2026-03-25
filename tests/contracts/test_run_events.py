from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel

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
