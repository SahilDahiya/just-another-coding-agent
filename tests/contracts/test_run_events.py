from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    CallToolsNode,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from pydantic_graph import End

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ShellActivityDetails,
)
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps


class _FakeCallToolsNode(CallToolsNode):
    def __init__(self, stream_factory) -> None:
        self._stream_factory = stream_factory

    @asynccontextmanager
    async def stream(self, _ctx):
        yield self._stream_factory()


class _FakeAgentRun:
    def __init__(self, *, next_node, next_result, result_holder) -> None:
        self.next_node = next_node
        self._next_result = next_result
        self._result_holder = result_holder
        self.ctx = object()

    @property
    def result(self):
        return self._result_holder.get("result")

    async def next(self, node):
        assert node is self.next_node
        return self._next_result


@asynccontextmanager
async def _iter_from_legacy_run_stream_events(
    agent,
    prompt: str,
    *,
    output_type=None,
    message_history=None,
    deps=None,
    model_settings=None,
    usage_limits=None,
    instructions=None,
):
    result_holder: dict[str, AgentRunResult] = {}

    async def _stream():
        async for event in agent.run_stream_events(
            prompt,
            output_type=output_type,
            message_history=message_history,
            deps=deps,
            model_settings=model_settings,
            usage_limits=usage_limits,
            instructions=instructions,
        ):
            if isinstance(event, AgentRunResultEvent):
                result_holder["result"] = event.result
                continue
            yield event

    yield _FakeAgentRun(
        next_node=_FakeCallToolsNode(_stream),
        next_result=End(None),
        result_holder=result_holder,
    )


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


async def git_check_stream(
    messages: object,
    _agent_info: object,
) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args='{"command": "git status --short", "timeout": 5}',
                tool_call_id="call-shell",
            )
        }
        return

    yield "done"


class RecordingStreamAgent:
    def __init__(self, *, model=None) -> None:
        self.last_model_settings = None
        self.last_usage_limits = None
        self.model = model
        self.last_deps = None

    @asynccontextmanager
    async def iter(
        self,
        prompt: str,
        *,
        output_type=None,
        message_history=None,
        deps=None,
        model_settings=None,
        usage_limits=None,
        instructions: object | None = None,
    ):
        async with _iter_from_legacy_run_stream_events(
            self,
            prompt,
            output_type=output_type,
            message_history=message_history,
            deps=deps,
            model_settings=model_settings,
            usage_limits=usage_limits,
            instructions=instructions,
        ) as run:
            yield run

    def parallel_tool_call_execution_mode(self, _mode: str):
        return nullcontext()

    async def run_stream_events(
        self,
        _prompt: str,
        *,
        output_type=None,
        message_history=None,
        deps=None,
        model_settings=None,
        usage_limits=None,
        instructions: object | None = None,
    ) -> AsyncIterator[object]:
        # output_type assertion removed
        assert message_history is None
        self.last_deps = deps
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


async def test_stream_run_events_attaches_backend_transcript_summary() -> None:
    agent = Agent(FunctionModel(stream_function=git_check_stream), output_type=str)

    @agent.tool_plain
    async def shell(command: str, timeout: int | None = None):
        return make_tool_return(
            return_value={"exit_code": 0, "output": ""},
            title=f"shell {command}",
            summary="command exited 0",
            details=ShellActivityDetails(
                command_preview=command,
                shell_family="posix",
                timeout=timeout,
                exit_code=0,
            ),
        )

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    terminal = events[-1]
    assert isinstance(terminal, RunSucceededEvent)
    assert terminal.transcript_summary is not None
    assert terminal.transcript_summary.had_work_activity is True
    assert terminal.transcript_summary.tool_call_count == 1
    assert len(terminal.transcript_summary.activity_groups) == 1
    group = terminal.transcript_summary.activity_groups[0]
    assert group.group_kind == "execution"
    assert group.group_label == "Shell"
    assert group.group_counts.shell == 1
    assert group.group_counts.tool == 1
    assert group.display_hint == "git status --short"
    assert group.outcome == "success"


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


async def test_stream_run_events_binds_run_id_into_workspace_session_scope(
    tmp_path,
) -> None:
    agent = RecordingStreamAgent()
    deps = WorkspaceDeps(
        workspace_root=tmp_path,
        session_scope=RunSessionScope(session_id="a" * 32),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=deps,
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert isinstance(agent.last_deps, WorkspaceDeps)
    assert agent.last_deps is not deps
    assert agent.last_deps.session_scope.session_id == "a" * 32
    assert agent.last_deps.session_scope.run_id == events[0].run_id
    assert agent.last_deps.tool_update_sink is not None


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
