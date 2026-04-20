import json
from collections.abc import AsyncIterator

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    RunSucceededEvent,
    SessionLifecycleEvent,
    SessionTurnContextStatusEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.rpc.stdio import handle_rpc_json_line
from just_another_coding_agent.runtime.workspace_trust import accept_workspace_trust
from just_another_coding_agent.session.jsonl import load_session

_RUN_EVENT_ADAPTER = TypeAdapter(RunEvent)
_SESSION_LIFECYCLE_EVENT_ADAPTER = TypeAdapter(SessionLifecycleEvent)


def _persisted_event_types(events: list[RunEvent]) -> list[str]:
    return [event.type for event in events if event.type != "assistant_text_delta"]


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


async def looping_edit_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) >= 7:
        yield "done"
        return

    yield {
        0: DeltaToolCall(
            name="edit",
            json_args=(
                '{"path": "note.txt", "old_text": "missing", "new_text": "agent"}'
            ),
            tool_call_id=f"call-edit-{len(messages)}",
        )
    }


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def _rpc_messages(
    *,
    request_payload: object,
    model,
    workspace_root,
    sessions_root,
) -> list[dict[str, object]]:
    request_line = json.dumps(request_payload)
    messages: list[dict[str, object]] = []

    async def emit_rpc_event(request_id: str, event) -> None:
        messages.append(
            {
                "type": "rpc_event",
                "id": request_id,
                "event": json.loads(event.model_dump_json()),
            }
        )

    async for line in handle_rpc_json_line(
        line=request_line,
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        emit_rpc_event=emit_rpc_event,
    ):
        messages.append(json.loads(line))

    return messages


async def _create_session_id(*, workspace_root, sessions_root) -> str:
    accept_workspace_trust(workspace_root)
    messages = await _rpc_messages(
        request_payload={
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages[0]["type"] == "rpc_response"
    session_id = str(messages[0]["response"]["session_id"])
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    assert session_path.exists()
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs == []
    return session_id


async def _collect_run_events(
    *,
    model,
    workspace_root,
    sessions_root,
    session_id: str,
    prompt: str,
) -> list[RunEvent | SessionLifecycleEvent]:
    messages = await _rpc_messages(
        request_payload={
            "id": "req-1",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": prompt},
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert [message["type"] for message in messages[:-1]] == ["rpc_event"] * (
        len(messages) - 1
    )
    assert messages[-1]["type"] == "rpc_response"
    parsed_events: list[RunEvent | SessionLifecycleEvent] = []
    for message in messages[:-1]:
        event = message["event"]
        if event["type"] == "session_turn_context_status":
            parsed_events.append(
                _SESSION_LIFECYCLE_EVENT_ADAPTER.validate_python(event)
            )
            continue
        parsed_events.append(_RUN_EVENT_ADAPTER.validate_python(event))
    return parsed_events


async def test_e2e_rpc_runtime_session_uses_explicit_workspace_root(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    events = await _collect_run_events(
        model=FunctionModel(stream_function=make_write_then_read_stream()),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        session_id=session_id,
        prompt="go",
    )

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello\n"

    write_result = events[3]
    assert isinstance(write_result, ToolCallSucceededEvent)
    assert write_result.tool_name == "write"
    assert write_result.result == f"Wrote {workspace_root / 'note.txt'}"

    read_result = events[5]
    assert isinstance(read_result, ToolCallSucceededEvent)
    assert read_result.tool_name == "read"
    assert read_result.result == "hello\n"

    terminal = events[-1]
    assert isinstance(terminal, RunSucceededEvent)
    assert terminal.output_text == "done"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].messages
    assert [event.type for event in loaded.runs[0].events] == _persisted_event_types(
        [
            event
            for event in events
            if not isinstance(event, SessionTurnContextStatusEvent)
        ]
    )


async def test_e2e_failure_round_trips_through_rpc_and_session(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    sessions_root = tmp_path / "sessions"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    events = await _collect_run_events(
        model=FunctionModel(stream_function=looping_edit_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        session_id=session_id,
        prompt="go",
    )

    assert [event.type for event in events] == [
        "session_turn_context_status",
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
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"

    tool_result = events[3]
    assert isinstance(tool_result, ToolCallSucceededEvent)
    assert tool_result.tool_name == "edit"
    assert tool_result.result == {
        "ok": False,
        "error_type": "ToolMatchError",
        "message": (
            "old_text must match exactly once in "
            f"{workspace_root / 'note.txt'}; found 0 occurrences"
        ),
    }

    third_result = events[7]
    assert isinstance(third_result, ToolCallSucceededEvent)
    assert third_result.result == tool_result.result

    terminal = events[-1]
    assert terminal.type == "run_succeeded"
    assert terminal.output_text == "done"
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello\nworld\n"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].messages
    assert [event.type for event in loaded.runs[0].events] == _persisted_event_types(
        [
            event
            for event in events
            if not isinstance(event, SessionTurnContextStatusEvent)
        ]
    )
