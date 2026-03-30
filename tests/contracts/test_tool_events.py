import json
from collections.abc import AsyncIterator
from contextlib import contextmanager

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.messages import ModelMessage, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

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
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import WorkspaceDeps

_SHELL_FAMILY = detect_default_shell_family()


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
    ) -> AsyncIterator[object]:
        # output_type assertion removed
        assert message_history is None
        assert deps is None
        assert model_settings is None
        assert usage_limits is not None
        for event in self._events:
            yield event

        if self._error is not None:
            raise self._error

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
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="lsshell",
                json_args="{}",
                tool_call_id="call-unknown-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="lsshell",
                json_args="{}",
                tool_call_id="call-unknown-2",
            )
        }
        return

    if len(messages) == 5:
        yield {
            0: DeltaToolCall(
                name="ls",
                json_args='{"path":"."}',
                tool_call_id="call-ls-1",
            )
        }
        return

    yield "done"


async def exhausting_unknown_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) in {1, 3, 5}:
        yield {
            0: DeltaToolCall(
                name="lsshell",
                json_args="{}",
                tool_call_id=f"call-unknown-{len(messages)}",
            )
        }
        return

    yield "done"


async def recovering_invalid_tool_args_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="ls",
                json_args='{"path":".","ignore":[".git"]}{"command":"pwd"}',
                tool_call_id="call-ls-invalid-1",
            )
        }
        return

    if len(messages) == 3:
        yield {
            0: DeltaToolCall(
                name="ls",
                json_args='{"path":"."}',
                tool_call_id="call-ls-valid-1",
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
    assert events[1].activity.group_kind == "exploration"
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "ToolPathError"
    assert "missing.txt" in events[2].result["message"]
    assert events[2].activity is not None
    assert events[2].activity.title == "read missing.txt"
    assert "missing.txt" in events[2].activity.summary
    assert events[2].activity.duration_ms is not None
    assert events[2].activity.duration_ms >= 0
    assert events[2].activity.details is None
    assert isinstance(events[3], ToolCallStartedEvent)
    assert events[3].activity is not None
    assert events[3].activity.title == "read note.txt"
    assert events[3].activity.group_kind == "exploration"
    assert isinstance(events[4], ToolCallSucceededEvent)
    assert events[4].result == "hello\nworld\n"
    assert events[4].activity is not None
    assert events[4].activity.title == "read note.txt"
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
    ]
    assert isinstance(events[6], ToolCallFailedEvent)
    assert events[6].tool_name == "lsshell"
    assert events[6].message == "Tool 'lsshell' exceeded max retries count of 2"
    assert isinstance(events[7], RunFailedEvent)
    assert events[7].message == "Tool 'lsshell' exceeded max retries count of 2"


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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
    assert isinstance(events[2], ToolCallSucceededEvent)
    assert events[2].result["ok"] is False
    assert events[2].result["error_type"] == "RetryPromptPart"
    assert "Invalid JSON" in events[2].result["message"]
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
        )
    ]

    assert events[0].type == "run_started"
    assert events[1].type == "tool_call_started"
    assert events[-2].type == "assistant_text_delta"
    assert events[-1].type == "run_succeeded"
    assert isinstance(events[1], ToolCallStartedEvent)
    assert events[1].activity == ToolActivity(title=f"shell {_sleep_command()}")
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
        title=f"shell {_ok_command()}"
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
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
