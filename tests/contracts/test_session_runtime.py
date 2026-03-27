import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import SessionCompactionSummary
from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.runtime.compaction import (
    summarize_session_for_compaction,
)
from just_another_coding_agent.session import (
    SessionFormatError,
    append_compaction_to_session,
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


def _system_prompt_contents(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, SystemPromptPart)
    ]


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


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


def model_driven_compaction_function(
    messages: list[ModelMessage],
    _agent_info: object,
) -> ModelResponse:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Run run-2" in prompt
    assert "Previous compaction summary:" in prompt
    assert "ship the first draft" in prompt
    assert "Run run-1" not in prompt

    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps(
                    {
                        "current_objective": "finish the second run",
                        "established_facts": [
                            "The earlier draft was shipped.",
                            "The second run is now the active context.",
                        ],
                        "user_preferences": ["be concise"],
                        "important_paths": ["note.txt", "src/app.py"],
                        "open_questions": ["Should we add retries?"],
                        "unresolved_work": ["Run the final acceptance check."],
                    }
                )
            )
        ]
    )


def auto_compaction_summary_function(
    messages: list[ModelMessage],
    _agent_info: object,
) -> ModelResponse:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Run run-5" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps(
                    {
                        "current_objective": "continue after auto compaction",
                        "established_facts": ["Five runs were summarized."],
                        "user_preferences": [],
                        "important_paths": ["note.txt"],
                        "open_questions": [],
                        "unresolved_work": ["Handle the follow-up prompt."],
                    }
                )
            )
        ]
    )


async def compacted_history_probe_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    all_user_prompts = [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, UserPromptPart)
    ]
    system_prompts = _system_prompt_contents(messages)

    if "first" in all_user_prompts:
        raise AssertionError("raw pre-compaction history should not be replayed")
    if "second" not in all_user_prompts:
        raise AssertionError("retained post-compaction history should be replayed")
    if "third" not in all_user_prompts:
        raise AssertionError("current prompt should be present")
    if not _has_tool_return(messages, tool_name="write"):
        raise AssertionError("retained post-compaction tool history should be replayed")
    if not any(
        prompt.startswith("Session compaction summary:")
        for prompt in system_prompts
    ):
        raise AssertionError("compaction summary should be injected")

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
    assert loaded.runs[0].thinking is None
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
        thinking=None,
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


async def test_stream_session_run_events_inherits_last_persisted_thinking_when_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking="high",
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        thinking=None,
    ):
        captured["prompt"] = prompt
        captured["thinking"] = thinking
        captured["message_history"] = message_history
        yield RunStartedEvent(run_id="run-2")
        yield RunSucceededEvent(run_id="run-2", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        fake_stream_run_events,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="second",
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert captured["prompt"] == "second"
    assert captured["thinking"] == "high"
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.thinking for run in loaded.runs] == ["high", "high"]
    assert loaded.thinking == "high"


async def test_stream_session_run_events_replays_compacted_history_keeps_messages_raw(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        summary=SessionCompactionSummary(
            current_objective="summarized first run",
            established_facts=["The first run completed."],
            user_preferences=[],
            important_paths=[],
            open_questions=[],
            unresolved_work=["Continue with the next run."],
        ),
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="second")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="write",
                        content="Wrote note.txt",
                        tool_call_id="call-write",
                    )
                ]
            ),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=compacted_history_probe_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="third",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    latest_messages = loaded.runs[-1].messages
    assert _system_prompt_contents(latest_messages) == []
    assert [run.prompt for run in loaded.runs] == ["first", "second", "third"]
    assert len(loaded.compactions) == 1


async def test_summarize_session_for_compaction_uses_model_output_and_prior_summary(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        summary=SessionCompactionSummary(
            current_objective="ship the first draft",
            established_facts=["The first draft was completed."],
            user_preferences=["be concise"],
            important_paths=["note.txt"],
            open_questions=[],
            unresolved_work=["Start the second run."],
        ),
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    summary = await summarize_session_for_compaction(
        model=FunctionModel(function=model_driven_compaction_function),
        loaded_session=loaded,
    )

    assert summary.current_objective == "finish the second run"
    assert summary.established_facts == [
        "The earlier draft was shipped.",
        "The second run is now the active context.",
    ]
    assert summary.user_preferences == ["be concise"]
    assert summary.important_paths == ["note.txt", "src/app.py"]
    assert summary.open_questions == ["Should we add retries?"]
    assert summary.unresolved_work == ["Run the final acceptance check."]


async def test_stream_session_run_events_auto_compacts_stale_session_before_resuming(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    for index in range(5):
        run_id = f"run-{index + 1}"
        append_run_to_session(
            path=session_path,
            workspace_root=workspace_root,
            prompt=f"prompt-{index + 1}",
            thinking=None,
            messages=[
                ModelRequest(parts=[UserPromptPart(content=f"prompt-{index + 1}")])
            ],
            events=[
                RunStartedEvent(run_id=run_id),
                RunSucceededEvent(run_id=run_id, output_text="done"),
            ],
        )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(
                function=auto_compaction_summary_function,
                stream_function=text_only_stream,
            ),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="follow-up",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert len(loaded.compactions) == 1
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.summarized_through_run_id == "run-5"
    assert (
        loaded.latest_compaction.summary.current_objective
        == "continue after auto compaction"
    )


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
