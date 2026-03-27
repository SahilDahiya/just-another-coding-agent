from collections.abc import AsyncIterator

from pydantic_ai import Agent, AgentRunResult, AgentRunResultEvent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.runtime.run import stream_run_events


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


class RecordingStreamAgent:
    def __init__(self) -> None:
        self.last_model_settings = None

    async def run_stream_events(
        self,
        _prompt: str,
        *,
        message_history=None,
        model_settings=None,
    ) -> AsyncIterator[object]:
        assert message_history is None
        self.last_model_settings = model_settings
        yield AgentRunResultEvent(result=AgentRunResult("done"))


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


async def test_stream_run_events_passes_thinking_as_model_settings() -> None:
    agent = RecordingStreamAgent()

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            thinking="high",
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert agent.last_model_settings == {"thinking": "high"}
