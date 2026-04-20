import json

import pytest
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.rpc.stdio import handle_rpc_json_line
from tests.contracts.rpc_stdio_test_support import (
    create_session_id,
    exploding_session_stream,
    noop_emit_rpc_event,
    rpc_messages,
    text_only_stream,
)


async def test_handle_rpc_json_line_returns_internal_error_for_unexpected_exception(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        exploding_session_stream,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-internal",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "go"},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-internal",
            "error_type": "InternalError",
            "message": "internal boom",
        }
    ]


async def test_handle_rpc_json_line_returns_invalid_json_error(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line="{",
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=noop_emit_rpc_event,
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
    assert not sessions_root.exists()


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
                "payload": {"session_id": "s", "prompt": "go"},
            },
            None,
        ),
        (
            {
                "id": 3,
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go"},
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
                "payload": {"prompt": "go"},
            },
            "req-8",
        ),
        (
            {
                "id": "req-9",
                "command": "run.start",
                "payload": {"session_id": 7, "prompt": "go"},
            },
            "req-9",
        ),
        (
            {
                "id": "req-10",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": 7},
            },
            "req-10",
        ),
        (
            {
                "id": "req-11",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go"},
                "extra": True,
            },
            "req-11",
        ),
        (
            {
                "id": "req-12",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go", "extra": True},
            },
            "req-12",
        ),
        (
            {
                "id": "req-12b",
                "command": "run.start",
                "payload": {
                    "session_id": "0" * 32,
                    "prompt": "go",
                    "thinking": "extreme",
                },
            },
            "req-12b",
        ),
        (
            {
                "id": "req-13",
                "command": "session.create",
                "payload": {"extra": True},
            },
            "req-13",
        ),
        (
            {
                "id": "req-14",
                "command": "session.create",
            },
            "req-14",
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
    sessions_root = tmp_path / "sessions"

    messages = await rpc_messages(
        request_payload=request_payload,
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": expected_id,
            "error_type": "InvalidRequest",
            "message": "Invalid RPC request",
        }
    ]
    assert not sessions_root.exists()
