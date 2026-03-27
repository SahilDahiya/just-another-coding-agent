from collections.abc import AsyncIterator

from pydantic_ai import Agent, AgentRunResult, AgentRunResultEvent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import WorkspaceDeps


async def hello_stream(_messages: object, _agent_info: object) -> AsyncIterator[str]:
    yield "hello "
    yield "world"


async def broken_stream(_messages: object, _agent_info: object) -> AsyncIterator[str]:
    raise RuntimeError("boom")
    if False:  # pragma: no cover
        yield ""


async def flaky_timeout_stream(
    _messages: object,
    _agent_info: object,
) -> AsyncIterator[str]:
    flaky_timeout_stream.attempts += 1
    if flaky_timeout_stream.attempts == 1:
        raise TimeoutError("temporary timeout")

    yield "done"


flaky_timeout_stream.attempts = 0


async def partial_timeout_stream(
    _messages: object,
    _agent_info: object,
) -> AsyncIterator[str]:
    partial_timeout_stream.attempts += 1
    yield "partial"
    raise TimeoutError("timed out after partial output")


partial_timeout_stream.attempts = 0


async def always_timeout_stream(
    _messages: object,
    _agent_info: object,
) -> AsyncIterator[str]:
    always_timeout_stream.attempts += 1
    raise TimeoutError("temporary timeout")
    if False:  # pragma: no cover
        yield ""


always_timeout_stream.attempts = 0


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
    def __init__(self, *, model=None) -> None:
        self.last_model_settings = None
        self.last_usage_limits = None
        self.model = model

    async def run_stream_events(
        self,
        _prompt: str,
        *,
        message_history=None,
        deps=None,
        model_settings=None,
        usage_limits=None,
    ) -> AsyncIterator[object]:
        assert message_history is None
        assert deps is None
        self.last_model_settings = model_settings
        self.last_usage_limits = usage_limits
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


async def test_stream_run_events_can_enable_openai_server_history() -> None:
    agent = RecordingStreamAgent(
        model=OpenAIResponsesModel(
            "gpt-5.3-codex",
            provider=OpenAIProvider(
                base_url="https://example.test/v1",
                api_key="test-key",
            ),
        )
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            enable_server_history=True,
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert agent.last_model_settings == {"openai_previous_response_id": "auto"}


async def test_stream_run_events_passes_explicit_unbounded_usage_limits() -> None:
    agent = RecordingStreamAgent()

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert agent.last_usage_limits == UsageLimits(
        request_limit=None,
        tool_calls_limit=None,
        input_tokens_limit=None,
        output_tokens_limit=None,
        total_tokens_limit=None,
    )


async def test_build_canonical_agent_retries_one_transient_pre_stream_failure(
    tmp_path,
    caplog,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    flaky_timeout_stream.attempts = 0
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=flaky_timeout_stream),
        workspace_root=workspace_root,
        tool_names=[],
    )

    with caplog.at_level("DEBUG"):
        events = [
            event
            async for event in stream_run_events(
                agent=agent,
                prompt="go",
                deps=WorkspaceDeps(workspace_root),
            )
        ]

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], AssistantTextDeltaEvent)
    assert events[1].delta == "done"
    assert isinstance(events[2], RunSucceededEvent)
    assert events[2].output_text == "done"
    assert flaky_timeout_stream.attempts == 2
    assert "Retrying transient pre-stream run failure" in caplog.text


async def test_build_canonical_agent_does_not_retry_after_partial_stream_output(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    partial_timeout_stream.attempts = 0
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=partial_timeout_stream),
        workspace_root=workspace_root,
        tool_names=[],
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(workspace_root),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_failed",
    ]
    assert isinstance(events[1], AssistantTextDeltaEvent)
    assert events[1].delta == "partial"
    assert isinstance(events[2], RunFailedEvent)
    assert events[2].error_type == "TimeoutError"
    assert partial_timeout_stream.attempts == 1


async def test_build_canonical_agent_retries_transient_failure_only_once(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    always_timeout_stream.attempts = 0
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=always_timeout_stream),
        workspace_root=workspace_root,
        tool_names=[],
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(workspace_root),
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_failed"]
    assert isinstance(events[1], RunFailedEvent)
    assert events[1].error_type == "TimeoutError"
    assert always_timeout_stream.attempts == 2
