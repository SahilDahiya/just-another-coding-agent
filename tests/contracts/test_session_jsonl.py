import json

import pytest
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
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
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agent = Agent(
        FunctionModel(stream_function=successful_tool_stream),
        output_type=str,
    )

    @agent.tool_plain
    async def add(a: int, b: int) -> int:
        return a + b

    with capture_run_messages() as messages:
        events = [event async for event in stream_run_events(agent=agent, prompt="go")]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        events=events,
        messages=messages,
    )
    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.header.version == 2
    assert loaded.header.workspace_root == str(workspace_root.resolve())
    assert len(loaded.runs) == 1
    assert loaded.runs[0].run_id == events[0].run_id
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].messages == messages
    assert loaded.runs[0].events == events
    assert loaded.message_history == messages


def test_append_run_to_session_appends_without_rewriting_header(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
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
    first_messages = [
        ModelRequest(parts=[UserPromptPart(content="first")]),
    ]
    second_messages = [
        ModelRequest(parts=[UserPromptPart(content="second")]),
        ModelResponse(parts=[TextPart(content="boom")]),
    ]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="first",
        events=first_events,
        messages=first_messages,
    )
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="second",
        events=second_events,
        messages=second_messages,
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    line_types = [json.loads(line)["type"] for line in lines]

    assert line_types.count("session_header") == 1
    assert line_types.count("session_run") == 2
    assert line_types.count("session_messages") == 2
    assert line_types.count("session_event") == 5

    loaded = load_session(path=path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["first", "second"]
    assert loaded.message_history == first_messages + second_messages


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


def test_load_session_fails_when_file_is_empty(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(SessionFormatError, match="Session file is empty"):
        load_session(path=path)


def test_load_session_fails_on_duplicate_run_id(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "session_header",
            "version": 2,
            "workspace_root": str(tmp_path.resolve()),
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "first"},
        {
            "type": "session_messages",
            "run_id": "run-1",
            "messages": [
                {
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": "first"}],
                    "timestamp": None,
                    "run_id": None,
                    "metadata": None,
                    "instructions": None,
                }
            ],
        },
        {
            "type": "session_event",
            "run_id": "run-1",
            "event": {"type": "run_started", "run_id": "run-1"},
        },
        {
            "type": "session_event",
            "run_id": "run-1",
            "event": {
                "type": "run_failed",
                "run_id": "run-1",
                "error_type": "RuntimeError",
                "message": "boom",
            },
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "second"},
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Duplicate session run_id: run-1"):
        load_session(path=path)


def test_load_session_fails_on_unsupported_header_version(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "version": 999,
                "workspace_root": str(tmp_path.resolve()),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="Unsupported session format version on line 1: 999",
    ):
        load_session(path=path)


def test_load_session_fails_when_run_event_order_is_invalid(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "session_header",
            "version": 2,
            "workspace_root": str(tmp_path.resolve()),
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "go"},
        {
            "type": "session_messages",
            "run_id": "run-1",
            "messages": [
                {
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": "go"}],
                    "timestamp": None,
                    "run_id": None,
                    "metadata": None,
                    "instructions": None,
                }
            ],
        },
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


def test_load_session_fails_when_header_has_no_workspace_root(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "session_header", "version": 2}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Invalid session entry on line 1"):
        load_session(path=path)


def test_load_session_fails_when_expected_workspace_root_mismatches(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    run_events = [
        RunStartedEvent(run_id="run-1"),
        RunSucceededEvent(run_id="run-1", output_text="done"),
    ]
    run_messages = [ModelRequest(parts=[UserPromptPart(content="go")])]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        events=run_events,
        messages=run_messages,
    )

    with pytest.raises(SessionFormatError, match="Session workspace_root mismatch"):
        load_session(path=path, workspace_root=other_workspace)


def test_load_session_fails_when_session_messages_are_missing(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "session_header",
            "version": 2,
            "workspace_root": str(tmp_path.resolve()),
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "go"},
        {
            "type": "session_event",
            "run_id": "run-1",
            "event": {"type": "run_started", "run_id": "run-1"},
        },
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

    with pytest.raises(
        SessionFormatError,
        match="session_run must be followed by exactly one session_messages entry",
    ):
        load_session(path=path)
