import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.rpc.stdio import handle_rpc_json_line


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


async def test_handle_rpc_json_line_streams_run_events() -> None:
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def add(a: int, b: int) -> int:
        return a + b

    request_line = json.dumps(
        {
            "id": "req-1",
            "command": "run.start",
            "payload": {"prompt": "go"},
        }
    )
    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(line=request_line, agent=agent)
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
    assert messages[1]["event"]["tool_name"] == "add"
    assert messages[2]["event"]["result"] == 3
    assert messages[4]["event"]["output_text"] == "done"


async def test_handle_rpc_json_line_keeps_run_failure_in_event_stream() -> None:
    agent = Agent(
        FunctionModel(stream_function=failing_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def explode() -> str:
        raise RuntimeError("tool boom")

    request_line = json.dumps(
        {
            "id": "req-2",
            "command": "run.start",
            "payload": {"prompt": "go"},
        }
    )
    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(line=request_line, agent=agent)
    ]

    assert [message["type"] for message in messages] == ["rpc_event"] * 4
    assert [message["event"]["type"] for message in messages] == [
        "run_started",
        "tool_call_started",
        "tool_call_failed",
        "run_failed",
    ]
    assert messages[-1]["event"]["message"] == "tool boom"


async def test_handle_rpc_json_line_returns_invalid_json_error() -> None:
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(line="{", agent=agent)
    ]

    assert messages == [
        {
            "type": "rpc_error",
            "id": None,
            "error_type": "InvalidJSON",
            "message": "Invalid JSON request",
        }
    ]


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
    request_payload: object,
    expected_id: str | None,
) -> None:
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )
    request_line = json.dumps(request_payload)

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(line=request_line, agent=agent)
    ]

    assert messages == [
        {
            "type": "rpc_error",
            "id": expected_id,
            "error_type": "InvalidRequest",
            "message": "Invalid RPC request",
        }
    ]
