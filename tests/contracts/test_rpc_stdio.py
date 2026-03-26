import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.rpc.stdio import handle_rpc_json_line
from pi_code_agent.session import load_session


async def successful_write_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path": "note.txt", "content": "hello\\n"}',
                tool_call_id="call-write",
            )
        }
        return

    yield "done"


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


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def test_handle_rpc_json_line_streams_run_events_via_session_runtime(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    request_line = json.dumps(
        {
            "id": "req-1",
            "command": "run.start",
            "payload": {"prompt": "go"},
        }
    )

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=FunctionModel(stream_function=successful_write_stream),
            workspace_root=workspace_root,
            session_path=session_path,
        )
    ]

    assert [message["type"] for message in messages] == ["rpc_event"] * 5
    assert [message["id"] for message in messages] == ["req-1"] * 5
    assert [message["event"]["type"] for message in messages] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[1]["event"]["tool_name"] == "write"
    assert messages[2]["event"]["result"] == f"Wrote {workspace_root / 'note.txt'}"
    assert messages[4]["event"]["output_text"] == "done"

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].prompt == "go"
    assert [event.type for event in loaded.runs[0].events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]


async def test_handle_rpc_json_line_keeps_run_failure_in_event_stream_and_session(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    session_path = tmp_path / "session.jsonl"
    request_line = json.dumps(
        {
            "id": "req-2",
            "command": "run.start",
            "payload": {"prompt": "go"},
        }
    )

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=FunctionModel(stream_function=failing_edit_stream),
            workspace_root=workspace_root,
            session_path=session_path,
        )
    ]

    assert [message["type"] for message in messages] == ["rpc_event"] * 4
    assert [message["event"]["type"] for message in messages] == [
        "run_started",
        "tool_call_started",
        "tool_call_failed",
        "run_failed",
    ]
    assert "found 0 occurrences" in messages[-1]["event"]["message"]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].prompt == "go"
    assert [event.type for event in loaded.runs[0].events] == [
        "run_started",
        "tool_call_started",
        "tool_call_failed",
        "run_failed",
    ]


async def test_handle_rpc_json_line_returns_invalid_json_error(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line="{",
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
        )
    ]

    assert messages == [
        {
            "type": "rpc_error",
            "id": None,
            "error_type": "InvalidJSON",
            "message": "Invalid JSON request",
        }
    ]
    assert not session_path.exists()


@pytest.mark.parametrize(
    ("request_payload", "expected_id"),
    [
        (
            {
                "id": "req-3",
                "command": "run.nope",
                "payload": {"prompt": "go"},
            },
            "req-3",
        ),
        (
            {
                "command": "run.start",
                "payload": {"prompt": "go"},
            },
            None,
        ),
        (
            {
                "id": 3,
                "command": "run.start",
                "payload": {"prompt": "go"},
            },
            None,
        ),
        (
            {
                "id": "req-4",
                "payload": {"prompt": "go"},
            },
            "req-4",
        ),
        (
            {
                "id": "req-5",
                "command": "run.start",
            },
            "req-5",
        ),
        (
            {
                "id": "req-6",
                "command": "run.start",
                "payload": "go",
            },
            "req-6",
        ),
        (
            {
                "id": "req-7",
                "command": "run.start",
                "payload": {},
            },
            "req-7",
        ),
        (
            {
                "id": "req-8",
                "command": "run.start",
                "payload": {"prompt": 7},
            },
            "req-8",
        ),
        (
            {
                "id": "req-9",
                "command": "run.start",
                "payload": {"prompt": "go"},
                "extra": True,
            },
            "req-9",
        ),
        (
            {
                "id": "req-10",
                "command": "run.start",
                "payload": {"prompt": "go", "extra": True},
            },
            "req-10",
        ),
        (
            [],
            None,
        ),
    ],
)
async def test_handle_rpc_json_line_returns_invalid_request_error(
    tmp_path,
    request_payload: object,
    expected_id: str | None,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    request_line = json.dumps(request_payload)

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
        )
    ]

    assert messages == [
        {
            "type": "rpc_error",
            "id": expected_id,
            "error_type": "InvalidRequest",
            "message": "Invalid RPC request",
        }
    ]
    assert not session_path.exists()
