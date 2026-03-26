import json
from collections.abc import AsyncIterator

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.contracts.run_events import (
    RunEvent,
    RunFailedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
)
from pi_code_agent.rpc.stdio import handle_rpc_json_line
from pi_code_agent.session.jsonl import load_session

_RUN_EVENT_ADAPTER = TypeAdapter(RunEvent)


def make_write_then_read_stream():
    call_count = 0

    async def write_then_read_stream(
        _messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args='{"path": "note.txt", "content": "hello\\n"}',
                    tool_call_id="call-write",
                )
            }
            return

        if call_count == 2:
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "note.txt"}',
                    tool_call_id="call-read",
                )
            }
            return

        yield "done"

    return write_then_read_stream


async def failing_edit_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    yield {
        0: DeltaToolCall(
            name="edit",
            json_args=(
                '{"path": "note.txt", "old_text": "missing", '
                '"new_text": "agent"}'
            ),
            tool_call_id="call-edit",
        )
    }


async def _collect_rpc_events(
    *,
    model,
    workspace_root,
    session_path,
    prompt: str,
) -> list[RunEvent]:
    request_line = json.dumps(
        {
            "id": "req-1",
            "command": "run.start",
            "payload": {"prompt": prompt},
        }
    )
    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
        )
    ]

    assert [message["type"] for message in messages] == ["rpc_event"] * len(messages)
    return [
        _RUN_EVENT_ADAPTER.validate_python(message["event"]) for message in messages
    ]


async def test_e2e_rpc_runtime_session_uses_explicit_workspace_root(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    events = await _collect_rpc_events(
        model=FunctionModel(stream_function=make_write_then_read_stream()),
        workspace_root=workspace_root,
        session_path=session_path,
        prompt="go",
    )

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello\n"

    write_result = events[2]
    assert isinstance(write_result, ToolCallSucceededEvent)
    assert write_result.tool_name == "write"
    assert write_result.result == f"Wrote {workspace_root / 'note.txt'}"

    read_result = events[4]
    assert isinstance(read_result, ToolCallSucceededEvent)
    assert read_result.tool_name == "read"
    assert read_result.result == "hello\n"

    terminal = events[-1]
    assert isinstance(terminal, RunSucceededEvent)
    assert terminal.output_text == "done"

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].messages
    assert loaded.runs[0].events == events


async def test_e2e_failure_round_trips_through_rpc_and_session(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    session_path = tmp_path / "failed-session.jsonl"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    events = await _collect_rpc_events(
        model=FunctionModel(stream_function=failing_edit_stream),
        workspace_root=workspace_root,
        session_path=session_path,
        prompt="go",
    )

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_failed",
        "run_failed",
    ]

    tool_failed = events[2]
    assert isinstance(tool_failed, ToolCallFailedEvent)
    assert tool_failed.tool_name == "edit"
    assert "found 0 occurrences" in tool_failed.message

    terminal = events[3]
    assert isinstance(terminal, RunFailedEvent)
    assert terminal.message == tool_failed.message
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello\nworld\n"

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].messages
    assert loaded.runs[0].events == events
