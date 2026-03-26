import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
)
from pi_code_agent.runtime.run import stream_run_events
from pi_code_agent.session.jsonl import (
    SessionFormatError,
    append_run_to_session,
    load_session,
)


async def successful_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
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


async def test_append_and_load_session_with_runtime_events(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def add(a: int, b: int) -> int:
        return a + b

    events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    append_run_to_session(path=path, prompt="go", events=events)
    loaded = load_session(path=path)

    assert loaded.header.version == 1
    assert len(loaded.runs) == 1
    assert loaded.runs[0].run_id == events[0].run_id
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].events == events


def test_append_run_to_session_appends_without_rewriting_header(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    first_events = [
        RunStartedEvent(run_id="run-1"),
        AssistantTextDeltaEvent(run_id="run-1", delta="hello"),
        RunSucceededEvent(run_id="run-1", output_text="hello"),
    ]
    second_events = [
        RunStartedEvent(run_id="run-2"),
        RunFailedEvent(
            run_id="run-2",
            error_type="RuntimeError",
            message="boom",
        ),
    ]

    append_run_to_session(path=path, prompt="first", events=first_events)
    append_run_to_session(path=path, prompt="second", events=second_events)

    lines = path.read_text(encoding="utf-8").splitlines()
    line_types = [json.loads(line)["type"] for line in lines]

    assert line_types.count("session_header") == 1
    assert line_types.count("session_run") == 2
    assert line_types.count("session_event") == 5

    loaded = load_session(path=path)
    assert [run.prompt for run in loaded.runs] == ["first", "second"]


def test_load_session_fails_without_header(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_run",
                "run_id": "run-1",
                "prompt": "go",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Session header must be first"):
        load_session(path=path)


def test_load_session_fails_when_run_event_order_is_invalid(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        {"type": "session_header", "version": 1},
        {"type": "session_run", "run_id": "run-1", "prompt": "go"},
        {
            "type": "session_event",
            "run_id": "run-1",
            "event": {
                "type": "run_succeeded",
                "run_id": "run-1",
                "output_text": "done",
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Run must start with run_started"):
        load_session(path=path)
