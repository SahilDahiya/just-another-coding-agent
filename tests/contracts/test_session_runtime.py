from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from pi_code_agent.contracts.run_events import (
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
)
from pi_code_agent.runtime import stream_session_run_events
from pi_code_agent.session import (
    SessionFormatError,
    append_run_to_session,
    load_session,
)


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        for part in message.parts:
            yield part


def _last_user_prompt(messages: list[ModelMessage]) -> str | None:
    prompt: str | None = None
    for part in _all_parts(messages):
        if isinstance(part, UserPromptPart):
            prompt = part.content
    return prompt


def _has_tool_return(messages: list[ModelMessage], *, tool_name: str) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        for part in _all_parts(messages)
    )


def make_write_stream():
    call_count = 0

    async def write_stream(_messages, _agent_info):
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

        yield "done"

    return write_stream


async def resume_aware_write_stream(messages, _agent_info):
    latest_prompt = _last_user_prompt(messages)
    saw_write = _has_tool_return(messages, tool_name="write")

    if latest_prompt == "create note" and not saw_write:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path": "note.txt", "content": "hello\\n"}',
                tool_call_id="call-write",
            )
        }
        return

    if latest_prompt == "create note" and saw_write:
        yield "created"
        return

    if latest_prompt == "what did you do?":
        if not saw_write:
            raise AssertionError("missing prior message history")
        yield "I created note.txt"
        return

    raise AssertionError(f"unexpected prompt: {latest_prompt!r}")


async def failing_edit_stream(_messages, _agent_info):
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


async def test_stream_session_run_events_persists_authoritative_session(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=make_write_stream()),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="go",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello\n"

    tool_succeeded = events[2]
    assert isinstance(tool_succeeded, ToolCallSucceededEvent)
    assert tool_succeeded.result == f"Wrote {workspace_root / 'note.txt'}"

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.header.workspace_root == str(workspace_root.resolve())
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].events == events
    assert loaded.runs[0].messages
    assert loaded.message_history == loaded.runs[0].messages


async def test_stream_session_run_events_rejects_mismatched_existing_workspace(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    before = session_path.read_text(encoding="utf-8")

    with pytest.raises(SessionFormatError, match="Session workspace_root mismatch"):
        _ = [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=make_write_stream()),
                workspace_root=other_workspace,
                session_path=session_path,
                prompt="second",
            )
        ]

    assert session_path.read_text(encoding="utf-8") == before


async def test_stream_session_run_events_resumes_with_pydanticai_message_history(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    model = FunctionModel(stream_function=resume_aware_write_stream)

    first_events = [
        event
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="create note",
        )
    ]
    second_events = [
        event
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="what did you do?",
        )
    ]

    first_terminal = first_events[-1]
    assert isinstance(first_terminal, RunSucceededEvent)
    assert first_terminal.output_text == "created"

    second_terminal = second_events[-1]
    assert isinstance(second_terminal, RunSucceededEvent)
    assert second_terminal.output_text == "I created note.txt"

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]
    assert _has_tool_return(loaded.message_history, tool_name="write")


async def test_stream_session_run_events_persists_failed_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    session_path = tmp_path / "session.jsonl"

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=failing_edit_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="go",
        )
    ]

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

    terminal = events[-1]
    assert isinstance(terminal, RunFailedEvent)
    assert terminal.message == tool_failed.message

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].events == events
    assert loaded.runs[0].messages


async def test_stream_session_run_events_does_not_persist_partial_consumption(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    stream = stream_session_run_events(
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        session_path=session_path,
        prompt="go",
    )

    first_event = await anext(stream)
    assert isinstance(first_event, RunStartedEvent)

    await stream.aclose()

    assert not session_path.exists()
