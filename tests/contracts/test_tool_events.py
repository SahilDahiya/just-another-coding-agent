from collections.abc import AsyncIterator

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.messages import ModelMessage, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

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


class StubStreamAgent:
    def __init__(
        self,
        *,
        events: list[object],
        error: Exception | None = None,
    ) -> None:
        self._events = events
        self._error = error

    async def run_stream_events(
        self,
        _prompt: str,
        *,
        message_history: list[ModelMessage] | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> AsyncIterator[object]:
        assert message_history is None
        assert usage_limits is not None
        for event in self._events:
            yield event

        if self._error is not None:
            raise self._error


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


async def test_stream_run_events_retry_prompt_emits_tool_failed_event() -> None:
    agent = StubStreamAgent(
        events=[
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "validate",
                    '{"value": 1}',
                    tool_call_id="call-validate",
                )
            ),
            FunctionToolResultEvent(
                result=RetryPromptPart(
                    content="bad input",
                    tool_name="validate",
                    tool_call_id="call-validate",
                )
            ),
            AgentRunResultEvent(result=AgentRunResult("done")),
        ]
    )

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallFailedEvent)
    assert isinstance(events[3], RunSucceededEvent)
    assert events[2].tool_call_id == "call-validate"
    assert events[2].tool_name == "validate"
    assert events[2].error_type == "RetryPromptPart"
    assert events[2].message == "bad input"
    assert events[3].output_text == "done"


async def test_stream_run_events_fails_hard_when_retry_prompt_has_no_pending_tool_call(
) -> None:
    agent = StubStreamAgent(
        events=[
            FunctionToolResultEvent(
                result=RetryPromptPart(
                    content="bad input",
                    tool_call_id="call-missing",
                )
            )
        ]
    )

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 2
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], RunFailedEvent)
    assert events[1].error_type == "RuntimeError"
    assert events[1].message == (
        "Tool result must match a pending tool_call_started: call-missing"
    )


async def test_stream_run_events_fails_hard_on_retry_prompt_tool_name_mismatch(
) -> None:
    agent = StubStreamAgent(
        events=[
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "read",
                    '{"path": "notes.txt"}',
                    tool_call_id="call-read",
                )
            ),
            FunctionToolResultEvent(
                result=RetryPromptPart(
                    content="wrong tool",
                    tool_name="write",
                    tool_call_id="call-read",
                )
            ),
            AgentRunResultEvent(result=AgentRunResult("done")),
        ]
    )

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallFailedEvent)
    assert isinstance(events[3], RunFailedEvent)
    assert events[2].tool_call_id == "call-read"
    assert events[2].tool_name == "read"
    assert events[2].error_type == "RuntimeError"
    assert events[2].message == (
        "Tool result tool_name mismatch for tool_call_id 'call-read': "
        "expected 'read', got 'write'"
    )
    assert events[3].message == events[2].message


async def test_stream_run_events_marks_all_pending_tool_calls_failed_before_run_failed(
) -> None:
    agent = StubStreamAgent(
        events=[
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "read",
                    '{"path": "notes.txt"}',
                    tool_call_id="call-read",
                )
            ),
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "bash",
                    '{"command": "pwd"}',
                    tool_call_id="call-bash",
                )
            ),
        ],
        error=RuntimeError("stream boom"),
    )

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert len(events) == 6
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallStartedEvent)
    assert isinstance(events[3], ToolCallFailedEvent)
    assert isinstance(events[4], ToolCallFailedEvent)
    assert isinstance(events[5], RunFailedEvent)
    assert [events[3].tool_call_id, events[4].tool_call_id] == [
        "call-read",
        "call-bash",
    ]
    assert [events[3].tool_name, events[4].tool_name] == ["read", "bash"]
    assert [events[3].message, events[4].message] == ["stream boom", "stream boom"]
    assert events[5].message == "stream boom"
