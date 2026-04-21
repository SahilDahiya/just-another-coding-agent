import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    CallToolsNode,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_graph import End

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolActivity,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import (
    RunRuntimeFrame,
    RunSessionScope,
    WorkspaceDeps,
)
from tests.read_only_worker_test_support import workspace_deps

_SHELL_FAMILY = detect_default_shell_family()


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


def _sleep_command() -> str:
    if _SHELL_FAMILY == "powershell":
        return "Start-Sleep -Seconds 2"
    return "sleep 2"


def _ok_command() -> str:
    if _SHELL_FAMILY == "powershell":
        return "[Console]::Out.Write('ok')"
    return "printf ok"


def _non_zero_command() -> str:
    if _SHELL_FAMILY == "powershell":
        return "[Console]::Error.Write('boom'); exit 7"
    return "printf boom >&2; exit 7"


def _streaming_command() -> str:
    if _SHELL_FAMILY == "powershell":
        return (
            "[Console]::Out.Write('one' + [Environment]::NewLine); "
            "[Console]::Out.Flush(); "
            "Start-Sleep -Milliseconds 50; "
            "[Console]::Out.Write('two' + [Environment]::NewLine); "
            "[Console]::Out.Flush()"
        )
    return (
        "python - <<'PY'\n"
        "import sys, time\n"
        "sys.stdout.write('one\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.05)\n"
        "sys.stdout.write('two\\n')\n"
        "sys.stdout.flush()\n"
        "PY"
    )


def _last_user_prompt(messages: list[ModelMessage]) -> str | None:
    prompt: str | None = None
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                prompt = part.content
    return prompt


def _user_prompt_count(messages: list[ModelMessage]) -> int:
    return sum(
        1
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


def _count_user_prompts_with_prefix(
    messages: list[ModelMessage],
    prefix: str,
) -> int:
    return sum(
        1
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart) and part.content.startswith(prefix)
    )


def _has_tool_return(messages: list[ModelMessage], *, tool_name: str) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        for message in messages
        for part in message.parts
    )


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
        output_type: object | None = None,
        message_history: list[ModelMessage] | None = None,
        deps: object | None = None,
        model_settings: object | None = None,
        usage_limits: object | None = None,
        instructions: object | None = None,
    ) -> AsyncIterator[object]:
        # output_type assertion removed
        assert message_history is None
        assert instructions is None
        assert deps is None
        assert model_settings is None
        assert usage_limits is not None
        for event in self._events:
            yield event

        if self._error is not None:
            raise self._error

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
        instructions=None,
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

    @staticmethod
    @contextmanager
    def parallel_tool_call_execution_mode(mode: str = "parallel"):
        assert mode == "parallel"
        yield


class LateToolUpdateAgent:
    async def run_stream_events(
        self,
        _prompt: str,
        *,
        output_type: object | None = None,
        message_history: list[ModelMessage] | None = None,
        deps: object | None = None,
        model_settings: object | None = None,
        usage_limits: object | None = None,
        instructions: object | None = None,
    ) -> AsyncIterator[object]:
        del output_type, message_history, model_settings, usage_limits, instructions
        assert isinstance(deps, WorkspaceDeps)
        assert deps.tool_update_sink is not None

        yield FunctionToolCallEvent(
            part=ToolCallPart(
                "shell",
                '{"command":"printf ok"}',
                tool_call_id="call-shell",
            )
        )
        await deps.tool_update_sink("call-shell", "shell", {"output": "running"})
        yield FunctionToolResultEvent(
            result=ToolReturnPart(
                tool_name="shell",
                content={"exit_code": 0, "output": "ok"},
                tool_call_id="call-shell",
            )
        )
        await deps.tool_update_sink("call-shell", "shell", {"output": "stale"})
        yield AgentRunResultEvent(result=AgentRunResult("done"))

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
        instructions=None,
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

    @staticmethod
    @contextmanager
    def parallel_tool_call_execution_mode(mode: str = "parallel"):
        assert mode == "parallel"
        yield


class RecoveringAfterProviderToolErrorAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[ModelMessage] | None]] = []

    async def run_stream_events(
        self,
        prompt: str,
        *,
        output_type: object | None = None,
        message_history: list[ModelMessage] | None = None,
        deps: object | None = None,
        model_settings: object | None = None,
        usage_limits: object | None = None,
        instructions: object | None = None,
    ) -> AsyncIterator[object]:
        del output_type, deps, model_settings, usage_limits, instructions
        self.calls.append((prompt, message_history))

        if len(self.calls) == 1:
            assert prompt == "go"
            assert message_history is None
            yield FunctionToolCallEvent(
                part=ToolCallPart(
                    "readreadread",
                    '{"path":"README.md"}',
                    tool_call_id="call-readreadread",
                )
            )
            yield FunctionToolResultEvent(
                result=RetryPromptPart(
                    content=(
                        "Unknown tool name: 'readreadread'. Available tools: 'read'"
                    ),
                    tool_name="readreadread",
                    tool_call_id="call-readreadread",
                )
            )
            raise RuntimeError("status_code: 400, invalid tool call arguments")

        assert len(self.calls) == 2
        assert prompt == "Unknown tool name: 'readreadread'. Available tools: 'read'"
        assert message_history is not None
        assert _last_user_prompt(message_history) == "go"
        yield FunctionToolCallEvent(
            part=ToolCallPart(
                "read",
                '{"path":"README.md"}',
                tool_call_id="call-read",
            )
        )
        yield FunctionToolResultEvent(
            result=ToolReturnPart(
                tool_name="read",
                content="# README",
                tool_call_id="call-read",
            )
        )
        yield AgentRunResultEvent(result=AgentRunResult("done"))

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
        instructions=None,
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

    @staticmethod
    @contextmanager
    def parallel_tool_call_execution_mode(mode: str = "parallel"):
        assert mode == "parallel"
        yield


class RetryPromptRecoveryAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[ModelMessage] | None]] = []

    async def run_stream_events(
        self,
        prompt: str,
        *,
        output_type: object | None = None,
        message_history: list[ModelMessage] | None = None,
        deps: object | None = None,
        model_settings: object | None = None,
        usage_limits: object | None = None,
        instructions: object | None = None,
    ) -> AsyncIterator[object]:
        del output_type, deps, model_settings, usage_limits, instructions
        self.calls.append((prompt, message_history))

        if len(self.calls) == 1:
            assert prompt == "go"
            assert message_history is None
            yield FunctionToolCallEvent(
                part=ToolCallPart(
                    "validate",
                    '{"value": 1}',
                    tool_call_id="call-validate",
                )
            )
            yield FunctionToolResultEvent(
                result=RetryPromptPart(
                    content="bad input",
                    tool_name="validate",
                    tool_call_id="call-validate",
                )
            )
            return

        assert len(self.calls) == 2
        assert prompt == "bad input"
        assert message_history is not None
        assert _last_user_prompt(message_history) == "go"
        yield AgentRunResultEvent(result=AgentRunResult("done"))

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
        instructions=None,
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

    @staticmethod
    @contextmanager
    def parallel_tool_call_execution_mode(mode: str = "parallel"):
        assert mode == "parallel"
        yield


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


async def recovering_edit_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="edit",
                json_args=(
                    '{"path":"note.txt","old_text":"missing","new_text":"agent"}'
                ),
                tool_call_id="call-edit-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="edit",
                json_args=('{"path":"note.txt","old_text":"world","new_text":"agent"}'),
                tool_call_id="call-edit-2",
            )
        }
        return

    yield "done"


async def recovering_read_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="read",
                json_args='{"path":"missing.txt"}',
                tool_call_id="call-read-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="read",
                json_args='{"path":"note.txt"}',
                tool_call_id="call-read-2",
            )
        }
        return

    yield "done"


async def recovering_unknown_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _has_tool_return(messages, tool_name="ls"):
        yield "done"
        return

    correction_count = _count_user_prompts_with_prefix(
        messages, "Unknown tool name:"
    )
    if correction_count < 2:
        yield {
            0: DeltaToolCall(
                name="lsshell",
                json_args="{}",
                tool_call_id="call-unknown-1",
            )
        }
        return

    yield {
        0: DeltaToolCall(
            name="ls",
            json_args='{"path":"."}',
            tool_call_id="call-ls-1",
        )
    }
    return



async def exhausting_unknown_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _count_user_prompts_with_prefix(messages, "Unknown tool name:") < 3:
        yield {
            0: DeltaToolCall(
                name="lsshell",
                json_args="{}",
                tool_call_id=(
                    "call-unknown-"
                    f"{_count_user_prompts_with_prefix(messages, 'Unknown tool name:')}"
                ),
            )
        }
        return

    yield "done"


async def recovering_invalid_tool_args_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _has_tool_return(messages, tool_name="ls"):
        yield "done"
        return

    if _count_user_prompts_with_prefix(messages, "Invalid ") == 0:
        yield {
            0: DeltaToolCall(
                name="ls",
                json_args='{"path":".","ignore":[".git"]}{"command":"pwd"}',
                tool_call_id="call-ls-invalid-1",
            )
        }
        return

    yield {
        0: DeltaToolCall(
            name="ls",
            json_args='{"path":"."}',
            tool_call_id="call-ls-valid-1",
        )
    }
    return


async def empty_string_ls_args_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _count_user_prompts_with_prefix(messages, "Invalid ") == 0:
        yield {
            0: DeltaToolCall(
                name="ls",
                json_args="",
                tool_call_id="call-ls-empty",
            )
        }
        return

    yield "done"


async def recovering_write_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path":"nested","content":"hello"}',
                tool_call_id="call-write-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path":"nested/note.txt","content":"hello"}',
                tool_call_id="call-write-2",
            )
        }
        return

    yield "done"


async def recovering_bash_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _sleep_command(), "timeout": 1}),
                tool_call_id="call-bash-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _ok_command()}),
                tool_call_id="call-bash-2",
            )
        }
        return

    yield "done"


async def recovering_non_zero_bash_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _non_zero_command()}),
                tool_call_id="call-bash-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _ok_command()}),
                tool_call_id="call-bash-2",
            )
        }
        return

    yield "done"


async def recovering_denied_approval_shell_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": "curl https://example.com"}),
                tool_call_id="call-bash-denied-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _ok_command()}),
                tool_call_id="call-bash-denied-2",
            )
        }
        return

    yield "done"


async def steer_aware_shell_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    latest_prompt = _last_user_prompt(messages)
    saw_shell = _has_tool_return(messages, tool_name="shell")

    if not saw_shell:
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": _sleep_command()}),
                tool_call_id="call-shell-steer",
            )
        }
        return

    if latest_prompt not in ("be concise", ["be concise"]):
        raise AssertionError(f"missing steer prompt, saw {latest_prompt!r}")

    yield "done steered"


async def streaming_bash_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        command = _streaming_command()
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": command}),
                tool_call_id="call-bash-stream",
            )
        }
        return

    yield "done"


async def streaming_bash_stream_with_steer_boundary(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        if _SHELL_FAMILY == "powershell":
            command = (
                "[Console]::Out.Write('one' + [Environment]::NewLine); "
                "[Console]::Out.Flush(); "
                "Start-Sleep -Milliseconds 300; "
                "[Console]::Out.Write('two' + [Environment]::NewLine); "
                "[Console]::Out.Flush()"
            )
        else:
            command = (
                "python - <<'PY'\n"
                "import sys, time\n"
                "sys.stdout.write('one\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(0.3)\n"
                "sys.stdout.write('two\\n')\n"
                "sys.stdout.flush()\n"
                "PY"
            )
        yield {
            0: DeltaToolCall(
                name="shell",
                json_args=json.dumps({"command": command}),
                tool_call_id="call-bash-stream-steer",
            )
        }
        return

    yield "done"


async def looping_edit_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    yield {
        0: DeltaToolCall(
            name="edit",
            json_args='{"path":"note.txt","old_text":"missing","new_text":"agent"}',
            tool_call_id=f"call-edit-{len(messages)}",
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

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=("add",),
        )
    ]

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
    assert events[1].activity == ToolActivity(title="add")
    assert events[2].tool_call_id == "call-add"
    assert events[2].tool_name == "add"
    assert events[2].result == 3
    assert events[2].activity is not None
    assert events[2].activity.title == "add"
    assert events[2].activity.duration_ms is not None
    assert events[2].activity.duration_ms >= 0
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

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=("explode",),
        )
    ]

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


async def test_stream_run_events_retry_prompt_emits_tool_error_result() -> None:
    agent = RetryPromptRecoveryAgent()

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=("validate",),
        )
    ]

    assert len(events) == 4
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert isinstance(events[3], RunSucceededEvent)
    assert events[2].tool_call_id == "call-validate"
    assert events[2].tool_name == "validate"
    assert events[2].result == {
        "ok": False,
        "error_type": "RetryPromptPart",
        "message": "bad input",
    }
    assert events[2].activity is not None
    assert events[2].activity.title == "validate"
    assert events[2].activity.summary == "bad input"
    assert events[2].activity.duration_ms is not None
    assert events[2].activity.duration_ms >= 0
    assert events[3].output_text == "done"


async def test_stream_run_events_restarts_after_provider_rejects_failed_tool_correction(
) -> None:
    agent = RecoveringAfterProviderToolErrorAgent()

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=("read",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].tool_name == "readreadread"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result == {
        "ok": False,
        "error_type": "RetryPromptPart",
        "message": "Unknown tool name: 'readreadread'. Available tools: 'read'",
    }
    assert isinstance(events[3], ToolCallStartedEvent)
    assert events[3].tool_name == "read"
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == "# README"
    assert isinstance(events[5], RunSucceededEvent)
    assert events[5].output_text == "done"


async def test_stream_run_events_uses_canonical_validated_args_for_empty_string_ls_call(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=empty_string_ls_args_stream),
        workspace_root=workspace_root,
        tool_names=("ls",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("ls",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].tool_name == "ls"
    assert events[1].args is None
    assert events[1].args_valid is False
    assert events[1].activity is not None
    assert events[1].activity.title == "ls"
    assert events[1].activity.display_label == "List"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "RetryPromptPart"
    assert events[2].result["message"] == (
        "Invalid JSON for tool 'ls': Expecting value at line 1 column 1. "
        "Fix the errors and try again."
    )
    assert isinstance(events[4], RunSucceededEvent)
    assert events[4].output_text == "done"


def make_subagent_stream():
    call_count = 0

    async def subagent_stream(_messages, _agent_info):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="subagent",
                    json_args=(
                        '{"name":"compaction-scan","role":"explore",'
                        '"task":"Find where compaction resets turn context."}'
                    ),
                    tool_call_id="call-subagent",
                )
            }
            return

        yield "done"

    return subagent_stream


async def test_stream_run_events_exposes_compact_subagent_activity(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = FunctionModel(stream_function=make_subagent_stream())
    agent = build_canonical_agent(
        model=model,
        workspace_root=workspace_root,
        tool_names=("subagent",),
    )

    async def fake_stream_ephemeral_subagent_run_events(**kwargs):
        assert kwargs["spec"].name == "compaction-scan"
        assert kwargs["spec"].role == "explore"
        assert kwargs["spec"].capability == "default"
        assert kwargs["spec"].parent_session_id == "a" * 32
        assert kwargs["spec"].parent_run_id is not None
        assert kwargs["spec"].parent_tool_call_id == "call-subagent"
        assert kwargs["thinking"] == "medium"
        yield RunStartedEvent(run_id="child-run-1")
        yield ToolCallStartedEvent(
            run_id="child-run-1",
            tool_call_id="child-call-1",
            tool_name="read",
            args={"path": "AGENTS.md"},
            args_valid=True,
            activity=ToolActivity(
                title="read AGENTS.md",
                display_label="Read",
                group_kind="exploration",
            ),
        )
        yield RunSucceededEvent(
            run_id="child-run-1",
            output_text=(
                "Found reset in runtime/compaction/resume.py\n"
                "Evidence:\n"
                "- Observed reset in runtime/compaction/resume.py\n"
                "Next: Trace the next resumed-run caller after compaction.\n"
            ),
        )

    monkeypatch.setattr(
        "just_another_coding_agent.tools.subagent."
        "stream_ephemeral_subagent_run_events",
        fake_stream_ephemeral_subagent_run_events,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(
                workspace_root=workspace_root,
                shell_family=_SHELL_FAMILY,
                session_scope=RunSessionScope(session_id="a" * 32),
                run_frame=RunRuntimeFrame(
                    model=model,
                    current_date=date(2026, 4, 10),
                    timezone="America/Los_Angeles",
                    thinking="medium",
                ),
            ),
            thinking="medium",
            available_tool_names=("subagent",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_updated",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].tool_name == "subagent"
    assert events[1].activity is not None
    assert events[1].activity.title == "subagent compaction-scan"
    assert events[1].activity.display_label == "Explore"
    assert isinstance(events[2], ToolCallUpdatedEvent)
    assert events[2].activity is not None
    assert events[2].activity.title == "subagent compaction-scan"
    assert events[2].activity.summary == "starting child run"
    assert events[2].activity.details is not None
    assert events[2].activity.details.model_dump(mode="python") == {
        "kind": "subagent",
        "name": "compaction-scan",
        "role": "explore",
        "spawn_mode": "fork",
        "capability": "default",
        "preview_lines": [],
        "preview_terminal": False,
    }
    assert isinstance(events[3], ToolCallSucceededEvent)
    assert events[3].result == {
        "ok": True,
        "name": "compaction-scan",
        "role": "explore",
        "spawn_mode": "fork",
        "capability": "default",
        "summary_text": "Found reset in runtime/compaction/resume.py",
        "output_text": (
            "Found reset in runtime/compaction/resume.py\n"
            "Evidence:\n"
            "- Observed reset in runtime/compaction/resume.py\n"
            "Next: Trace the next resumed-run caller after compaction.\n"
        ),
    }
    assert events[3].activity is not None
    assert events[3].activity.title == "subagent compaction-scan"
    assert events[3].activity.display_label == "Explore"
    assert (
        events[3].activity.summary
        == "Found reset in runtime/compaction/resume.py"
    )
    assert events[3].activity.details is not None
    assert events[3].activity.details.model_dump(mode="python") == {
        "kind": "subagent",
        "name": "compaction-scan",
        "role": "explore",
        "spawn_mode": "fork",
        "capability": "default",
        "preview_lines": [
            "read AGENTS.md",
            "Found reset in runtime/compaction/resume.py",
        ],
        "preview_terminal": True,
    }
    assert isinstance(events[5], RunSucceededEvent)
    assert events[5].output_text == "done"


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

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
        )
    ]

    assert len(events) == 2
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], RunFailedEvent)
    assert events[1].error_type == "RuntimeError"
    assert events[1].message == (
        "Tool result must match a pending tool_call_started: call-missing"
    )


async def test_stream_run_events_fails_hard_on_retry_prompt_tool_name_mismatch() -> (
    None
):
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

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
        )
    ]

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
                    "shell",
                    '{"command": "pwd"}',
                    tool_call_id="call-bash",
                )
            ),
        ],
        error=RuntimeError("stream boom"),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
        )
    ]

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
    assert [events[3].tool_name, events[4].tool_name] == ["read", "shell"]
    assert [events[3].message, events[4].message] == ["stream boom", "stream boom"]
    assert events[5].message == "stream boom"


async def test_stream_run_events_rejects_terminal_success_with_unresolved_tool_call(
) -> None:
    agent = StubStreamAgent(
        events=[
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "read",
                    '{"path":"README.md"}',
                    tool_call_id="call-read",
                )
            ),
            FunctionToolCallEvent(
                part=ToolCallPart(
                    "shell",
                    '{"command":"pwd"}',
                    tool_call_id="call-shell",
                )
            ),
            FunctionToolResultEvent(
                result=ToolReturnPart(
                    tool_name="read",
                    content="# README",
                    tool_call_id="call-read",
                )
            ),
            AgentRunResultEvent(result=AgentRunResult("done")),
        ]
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_failed",
        "run_failed",
    ]
    assert isinstance(events[4], ToolCallFailedEvent)
    assert events[4].tool_call_id == "call-shell"
    assert events[4].tool_name == "shell"
    assert events[4].error_type == "SessionFormatError"
    assert events[4].message == "Run cannot terminate with unresolved tool calls"
    assert isinstance(events[5], RunFailedEvent)
    assert events[5].error_type == "SessionFormatError"
    assert events[5].message == "Run cannot terminate with unresolved tool calls"


async def test_stream_run_events_recovers_from_edit_mismatch_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    note = workspace_root / "note.txt"
    note.write_text("hello\nworld\n", encoding="utf-8")

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_edit_stream),
        workspace_root=workspace_root,
        tool_names=("edit",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("edit",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert isinstance(events[3], ToolCallStartedEvent)
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert isinstance(events[5], AssistantTextDeltaEvent)
    assert isinstance(events[6], RunSucceededEvent)
    assert events[2].tool_call_id == "call-edit-1"
    assert events[2].tool_name == "edit"
    assert events[2].result == {
        "ok": False,
        "error_type": "ToolMatchError",
        "message": f"old_text must match exactly once in {note}; found 0 occurrences",
    }
    assert events[4].tool_call_id == "call-edit-2"
    assert events[4].tool_name == "edit"
    assert events[4].result == f"Edited {note}"
    assert events[4].activity is not None
    assert events[4].activity.title == f"edit {note.name}"
    assert events[4].activity.summary == "edit applied"
    assert events[4].activity.details is not None
    assert events[4].activity.details.model_dump() == {
        "kind": "edit",
        "path": "note.txt",
        "diff": (f"--- {note}\n+++ {note}\n@@ -1,2 +1,2 @@\n hello\n-world\n+agent\n"),
        "added_lines": 1,
        "removed_lines": 1,
    }
    assert events[6].output_text == "done"
    assert note.read_text(encoding="utf-8") == "hello\nagent\n"


async def test_stream_run_events_recovers_from_missing_read_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    note = workspace_root / "note.txt"
    note.write_text("hello\nworld\n", encoding="utf-8")

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_read_stream),
        workspace_root=workspace_root,
        tool_names=("read",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("read",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].activity is not None
    assert events[1].activity.title == "read missing.txt"
    assert events[1].activity.display_label == "Read"
    assert events[1].activity.group_kind == "exploration"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "ToolPathError"
    assert "missing.txt" in events[2].result["message"]
    assert events[2].activity is not None
    assert events[2].activity.title == "read missing.txt"
    assert events[2].activity.display_label == "Read"
    assert "missing.txt" in events[2].activity.summary
    assert events[2].activity.duration_ms is not None
    assert events[2].activity.duration_ms >= 0
    assert events[2].activity.details is None
    assert isinstance(events[3], ToolCallStartedEvent)
    assert events[3].activity is not None
    assert events[3].activity.title == "read note.txt"
    assert events[3].activity.display_label == "Read"
    assert events[3].activity.group_kind == "exploration"
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == "hello\nworld\n"
    assert events[4].activity is not None
    assert events[4].activity.title == "read note.txt"
    assert events[4].activity.display_label == "Read"
    assert events[4].activity.summary == "read completed"
    assert events[4].activity.duration_ms is not None
    assert events[4].activity.duration_ms >= 0
    assert events[4].activity.details is not None
    assert events[4].activity.details.model_dump() == {
        "kind": "read",
        "path": "note.txt",
        "short_path": "note.txt",
        "offset": None,
        "limit": None,
    }
    assert events[6].output_text == "done"


async def test_stream_run_events_recovers_from_unknown_tool_name_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_unknown_tool_stream),
        workspace_root=workspace_root,
        tool_names=("ls",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("ls",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].tool_name == "lsshell"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result == {
        "ok": False,
        "error_type": "RetryPromptPart",
        "message": "Unknown tool name: 'lsshell'. Available tools: 'ls'",
    }
    assert isinstance(events[3], ToolCallStartedEvent)
    assert events[3].tool_name == "lsshell"
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == {
        "ok": False,
        "error_type": "RetryPromptPart",
        "message": "Unknown tool name: 'lsshell'. Available tools: 'ls'",
    }
    assert isinstance(events[5], ToolCallStartedEvent)
    assert events[5].tool_name == "ls"
    assert events[6].result == "(empty directory)"
    assert events[8].output_text == "done"


async def test_stream_run_events_fails_cleanly_when_unknown_tool_budget_is_exhausted(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=exhausting_unknown_tool_stream),
        workspace_root=workspace_root,
        tool_names=("ls",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("ls",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_failed",
        "run_failed",
        "run_failed",
    ]
    assert isinstance(events[6], ToolCallFailedEvent)
    assert events[6].tool_name == "lsshell"
    assert events[6].message == "Tool 'lsshell' exceeded max retries count of 2"
    assert isinstance(events[7], RunFailedEvent)
    assert events[7].message == "Tool 'lsshell' exceeded max retries count of 2"
    assert isinstance(events[8], RunFailedEvent)
    assert events[8].message == "Tool 'lsshell' exceeded max retries count of 0"


async def test_stream_run_events_recovers_from_invalid_tool_args_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_invalid_tool_args_stream),
        workspace_root=workspace_root,
        tool_names=("ls",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
            available_tool_names=("ls",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].tool_name == "ls"
    assert events[1].args is None
    assert events[1].args_valid is False
    assert events[1].activity is not None
    assert events[1].activity.title == "ls"
    assert events[1].activity.display_label == "List"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "RetryPromptPart"
    assert events[2].result["message"] == (
        "Invalid JSON for tool 'ls': Extra data at line 1 column 31. "
        "Fix the errors and try again."
    )
    assert isinstance(events[3], ToolCallStartedEvent)
    assert events[3].tool_name == "ls"
    assert events[3].args == {"path": "."}
    assert events[3].args_valid is True
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == "(empty directory)"
    assert isinstance(events[6], RunSucceededEvent)
    assert events[6].output_text == "done"


async def test_stream_run_events_recovers_from_write_directory_error_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    nested = workspace_root / "nested"
    nested.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_write_stream),
        workspace_root=workspace_root,
        tool_names=("write",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "ToolPathError"
    assert "nested" in events[2].result["message"]
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == f"Wrote {nested / 'note.txt'}"
    assert (nested / "note.txt").read_text(encoding="utf-8") == "hello"
    assert events[6].output_text == "done"


async def test_stream_run_events_recovers_from_bash_timeout_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_bash_stream),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
        )
    ]

    assert events[0].type == "run_started"
    assert events[1].type == "tool_call_started"
    assert events[-2].type == "assistant_text_delta"
    assert events[-1].type == "run_succeeded"
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].activity == ToolActivity(
        title=f"shell {_sleep_command()}",
        display_label="Shell",
    )
    first_result_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[first_result_index], ToolCallSucceededEvent)
    assert events[first_result_index].result == {
        "ok": False,
        "error_type": "ToolCommandError",
        "message": "Command timed out after 1 seconds",
    }
    assert events[first_result_index].activity is not None
    assert events[first_result_index].activity.title == f"shell {_sleep_command()}"
    assert (
        events[first_result_index].activity.summary
        == "Command timed out after 1 seconds"
    )
    assert events[first_result_index].activity.duration_ms is not None
    assert events[first_result_index].activity.duration_ms >= 0
    assert events[first_result_index].activity.details is None
    second_started_index = next(
        index
        for index, event in enumerate(events)
        if index > first_result_index and isinstance(event, ToolCallStartedEvent)
    )
    assert isinstance(events[second_started_index], ToolCallStartedEvent)
    assert events[second_started_index].activity == ToolActivity(
        title=f"shell {_ok_command()}",
        display_label="Shell",
    )
    second_result_index = next(
        index
        for index, event in enumerate(events)
        if index > second_started_index and isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[second_result_index], ToolCallSucceededEvent)
    assert events[second_result_index].result == {"exit_code": 0, "output": "ok"}
    assert events[second_result_index].activity is not None
    assert events[second_result_index].activity.title == f"shell {_ok_command()}"
    assert events[second_result_index].activity.summary == "command exited 0"
    assert events[second_result_index].activity.duration_ms is not None
    assert events[second_result_index].activity.duration_ms >= 0
    assert events[second_result_index].activity.details is not None
    assert events[second_result_index].activity.details.model_dump() == {
        "kind": "shell",
        "command_preview": _ok_command(),
        "shell_family": _SHELL_FAMILY,
        "timeout": None,
        "exit_code": 0,
    }
    assert events[-1].output_text == "done"


async def test_stream_run_events_recovers_from_non_zero_bash_exit_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_non_zero_bash_stream),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
        )
    ]

    assert events[0].type == "run_started"
    assert events[1].type == "tool_call_started"
    assert events[-2].type == "assistant_text_delta"
    assert events[-1].type == "run_succeeded"
    first_result_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[first_result_index], ToolCallSucceededEvent)
    assert events[first_result_index].result["ok"] is False
    assert events[first_result_index].result["error_type"] == "ToolCommandError"
    assert events[first_result_index].result["message"].replace("\r\n", "\n") == (
        "boom\n\nCommand exited with code 7"
    )
    second_result_index = next(
        index
        for index, event in enumerate(events)
        if index > first_result_index and isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[second_result_index], ToolCallSucceededEvent)
    assert events[second_result_index].result == {
        "exit_code": 0,
        "output": "ok",
    }
    assert events[-1].output_text == "done"


async def test_stream_run_events_recovers_from_denied_shell_approval_within_one_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=recovering_denied_approval_shell_stream),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    async def approval_requester(request) -> ApprovalDecision:
        return ApprovalDecision(
            request_id=request.request_id,
            decision="denied",
        )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(
                workspace_root=workspace_root,
                shell_family=_SHELL_FAMILY,
                permission_state=build_permission_state(
                    sandbox_policy=WorkspaceWriteSandboxPolicy(),
                    approval_policy=ApprovalPolicy(mode="on_escalation"),
                ),
            ),
            resolve_approval_request=approval_requester,
        )
    ]

    assert events[0].type == "run_started"
    assert events[1].type == "tool_call_started"
    assert events[-2].type == "assistant_text_delta"
    assert events[-1].type == "run_succeeded"
    first_result_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[first_result_index], ToolCallSucceededEvent)
    assert events[first_result_index].result == {
        "ok": False,
        "outcome": "denied",
        "denial_type": "approval_denied",
        "message": (
            "Approval denied: allow shell command: curl https://example.com "
            "(network enabled). The command was not run. "
            "Choose another approach or stop."
        ),
    }
    second_result_index = next(
        index
        for index, event in enumerate(events)
        if index > first_result_index and isinstance(event, ToolCallSucceededEvent)
    )
    assert isinstance(events[second_result_index], ToolCallSucceededEvent)
    assert events[second_result_index].result == {
        "exit_code": 0,
        "output": "ok",
    }
    assert events[-1].output_text == "done"


async def test_stream_run_events_emits_bash_tool_updates(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=streaming_bash_stream),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=workspace_deps(workspace_root),
        )
    ]

    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[-2], AssistantTextDeltaEvent)
    assert isinstance(events[-1], RunSucceededEvent)

    update_events = [
        event for event in events if isinstance(event, ToolCallUpdatedEvent)
    ]
    assert update_events
    assert update_events[0].tool_call_id == "call-bash-stream"
    assert update_events[0].tool_name == "shell"
    assert update_events[0].partial_result is not None
    assert update_events[0].activity is not None
    assert update_events[0].activity.title.startswith("shell ")
    assert update_events[0].activity.summary == "command still running"
    assert update_events[0].activity.duration_ms is not None
    assert update_events[0].activity.duration_ms >= 0
    assert update_events[0].activity.details is None

    final_tool_event = next(
        event for event in reversed(events) if isinstance(event, ToolCallSucceededEvent)
    )
    assert final_tool_event.tool_call_id == "call-bash-stream"
    assert final_tool_event.result["exit_code"] == 0
    assert final_tool_event.result["output"].replace("\r\n", "\n") == "one\ntwo\n"


async def test_stream_run_events_ignores_stale_tool_update_after_result(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    events = [
        event
        async for event in stream_run_events(
            agent=LateToolUpdateAgent(),
            prompt="go",
            deps=workspace_deps(workspace_root),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_updated",
        "tool_call_succeeded",
        "run_succeeded",
    ]
    update_events = [
        event for event in events if isinstance(event, ToolCallUpdatedEvent)
    ]
    assert len(update_events) == 1
    assert update_events[0].partial_result == {"output": "running"}
    terminal = events[-1]
    assert isinstance(terminal, RunSucceededEvent)
    assert terminal.output_text == "done"


async def test_stream_run_events_injects_pending_steer_after_tool_phase_completes(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=steer_aware_shell_stream),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    steer_boundary_ready: asyncio.Future[object] = asyncio.Future()
    pending_prompts: list[str] = []
    attached_prompts: list[str] = []

    async def activate_steer_boundary(attach) -> None:
        if not steer_boundary_ready.done():
            steer_boundary_ready.set_result(attach)

    async def submit_steer_boundary() -> None:
        steer_attach = await steer_boundary_ready
        attached_prompts[:] = list(pending_prompts)
        steer_attach(list(pending_prompts))

    async def deactivate_steer_boundary() -> None:
        return None

    async def queue_steer() -> None:
        await steer_boundary_ready
        pending_prompts.append("be concise")

    steer_task = asyncio.create_task(queue_steer())
    try:
        events = [
            event
            async for event in stream_run_events(
                agent=agent,
                prompt="go",
                deps=workspace_deps(workspace_root),
                activate_steer_boundary=activate_steer_boundary,
                submit_steer_boundary=submit_steer_boundary,
                deactivate_steer_boundary=deactivate_steer_boundary,
            )
        ]
    finally:
        if not steer_task.done():
            steer_task.cancel()
        with suppress(asyncio.CancelledError):
            await steer_task

    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert isinstance(events[-1], RunSucceededEvent)
    assert attached_prompts == ["be concise"]
    assert events[-1].output_text == "done steered"


async def test_stream_run_events_emits_bash_tool_updates_during_steer_boundary(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=streaming_bash_stream_with_steer_boundary),
        workspace_root=workspace_root,
        tool_names=("shell",),
    )

    async def activate_steer_boundary(_attach) -> None:
        return None

    async def submit_steer_boundary() -> None:
        return None

    async def deactivate_steer_boundary() -> None:
        return None

    stream = stream_run_events(
        agent=agent,
        prompt="go",
        deps=workspace_deps(workspace_root),
        activate_steer_boundary=activate_steer_boundary,
        submit_steer_boundary=submit_steer_boundary,
        deactivate_steer_boundary=deactivate_steer_boundary,
    )

    assert isinstance(await anext(stream), RunStartedEvent)
    assert isinstance(await anext(stream), ToolCallStartedEvent)
    timeout_seconds = 1.0
    update_event = await asyncio.wait_for(anext(stream), timeout=timeout_seconds)
    assert isinstance(update_event, ToolCallUpdatedEvent)
    assert update_event.tool_call_id == "call-bash-stream-steer"
    assert update_event.partial_result is not None

    remaining_events = [event async for event in stream]
    assert isinstance(remaining_events[-2], AssistantTextDeltaEvent)
    assert isinstance(remaining_events[-1], RunSucceededEvent)
