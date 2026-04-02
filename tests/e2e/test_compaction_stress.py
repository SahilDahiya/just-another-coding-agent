import asyncio
import io
import json
from collections.abc import AsyncIterator
from contextlib import contextmanager

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

import just_another_coding_agent.runtime.session as runtime_session_module
from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
)
from just_another_coding_agent.contracts.session import SessionCompactionSummary
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.runtime.compaction import (
    build_resume_instructions,
    build_resume_message_history,
)
from just_another_coding_agent.runtime.compaction import (
    session_summary as session_summary_module,
)
from just_another_coding_agent.runtime.compaction import trigger as trigger_module
from just_another_coding_agent.runtime.compaction.history_processors import (
    CompactionHistoryRuntime,
)
from just_another_coding_agent.runtime.compaction.in_run import (
    IN_RUN_COMPACTION_METADATA_KEY,
    build_in_run_compaction_controller,
)
from just_another_coding_agent.runtime.session import stream_session_run_events
from just_another_coding_agent.session import (
    SessionFormatError,
    append_compaction_to_session,
    append_run_to_session,
    initialize_session,
    load_session,
)


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        for part in message.parts:
            yield part


def _user_prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, UserPromptPart)
    ]


def _message_shapes(messages: list[ModelMessage]) -> list[str]:
    return [
        f"{type(message).__name__}:{[type(part).__name__ for part in message.parts]}"
        for message in messages
    ]


async def _text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def _serve_lines(
    *,
    model: FunctionModel,
    workspace_root,
    sessions_root,
    lines: list[dict[str, object]],
):
    input_stream = io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n")
    output_stream = io.StringIO()
    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    return [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]


async def _append_auto_compaction_summary(
    *,
    path,
    workspace_root,
):
    loaded = load_session(path=path, workspace_root=workspace_root)
    target = session_summary_module._build_auto_compaction_target(loaded)
    summary = SessionCompactionSummary(
        current_objective="continue after compaction",
        current_plan=["handle the follow-up prompt"],
        established_facts=["Older history was compacted."],
        unresolved_work=["answer the new prompt"],
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
        summarized_through_run_id=target.summarized_through_run_id,
        first_kept_run_id=target.first_kept_run_id,
        checkpoint_messages=target.checkpoint_messages,
    )


async def test_e2e_stdio_auto_compaction_keeps_recent_tail_and_new_run_is_delta_only(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "1" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    initialize_session(path=session_path, workspace_root=workspace_root)

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="A" * 120_000,
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="A" * 120_000)])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    for run_id, prompt in [("run-2", "second"), ("run-3", "third")]:
        append_run_to_session(
            path=session_path,
            workspace_root=workspace_root,
            prompt=prompt,
            thinking=None,
            messages=[ModelRequest(parts=[UserPromptPart(content=prompt)])],
            events=[
                RunStartedEvent(run_id=run_id),
                RunSucceededEvent(run_id=run_id, output_text="done"),
            ],
        )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return await _append_auto_compaction_summary(
            path=path,
            workspace_root=workspace_root,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.build_auto_compact_session_budget_report",
        lambda loaded_session, *, model: (
            trigger_module.build_auto_compact_session_budget_report(
                loaded_session,
                model=model,
                get_context_window_tokens=lambda _model: 2_000,
            )
        ),
    )

    messages = await _serve_lines(
        model=FunctionModel(stream_function=_text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        lines=[
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "follow-up",
                },
            }
        ],
    )

    event_types = [
        message["event"]["type"]
        for message in messages
        if message["type"] == "rpc_event"
    ]
    assert event_types == [
        "session_compaction_started",
        "session_compaction_completed",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.summarized_through_run_id == "run-1"
    assert loaded.latest_compaction.first_kept_run_id == "run-2"
    assert _user_prompts(loaded.latest_compaction.checkpoint_messages) == [
        "second",
        "third",
    ]
    assert _user_prompts(build_resume_message_history(loaded)) == [
        "second",
        "third",
        "follow-up",
    ]
    resume_instructions = build_resume_instructions(loaded)
    assert resume_instructions is not None
    assert "Current objective: continue after compaction" in resume_instructions
    assert _user_prompts(loaded.runs[-1].messages) == ["follow-up"]


async def test_e2e_stdio_resume_uses_same_run_split_tail_checkpoint(
    tmp_path,
) -> None:
    fixed_session_id = "2" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    initialize_session(path=session_path, workspace_root=workspace_root)

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
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="Y" * 25_000)]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args='{"path":"note.txt"}',
                        tool_call_id="call-read",
                    )
                ],
                model_name="test",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        tool_call_id="call-read",
                        content="note body",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="done")], model_name="test"),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )

    original_tail_budget = (
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS
    )
    try:
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS = 400
        await _append_auto_compaction_summary(
            path=session_path,
            workspace_root=workspace_root,
        )
    finally:
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS = (
            original_tail_budget
        )

    observed: dict[str, object] = {}

    async def probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["incoming_shapes"] = _message_shapes(messages)
        observed["incoming_user_prompts"] = _user_prompts(messages)
        yield "done"

    messages = await _serve_lines(
        model=FunctionModel(stream_function=probe_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        lines=[
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "after-split-tail",
                },
            }
        ],
    )

    event_types = [
        message["event"]["type"]
        for message in messages
        if message["type"] == "rpc_event"
    ]
    assert event_types == ["run_started", "assistant_text_delta", "run_succeeded"]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.summarized_through_run_id == "run-2"
    assert loaded.latest_compaction.first_kept_run_id == "run-2"
    assert _message_shapes(loaded.latest_compaction.checkpoint_messages) == [
        "ModelResponse:['ToolCallPart']",
        "ModelRequest:['ToolReturnPart']",
        "ModelResponse:['TextPart']",
    ]
    assert observed["incoming_user_prompts"] == ["after-split-tail"]
    resume_instructions = build_resume_instructions(loaded)
    assert resume_instructions is not None
    assert "Current objective: continue after compaction" in resume_instructions
    assert observed["incoming_shapes"][:3] == [
        "ModelResponse:['ToolCallPart']",
        "ModelRequest:['ToolReturnPart']",
        "ModelResponse:['TextPart']",
    ]
    assert _user_prompts(loaded.runs[-1].messages) == ["after-split-tail"]


async def test_e2e_stdio_unsafe_suffix_falls_back_to_summary_only_checkpoint(
    tmp_path,
) -> None:
    fixed_session_id = "3" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    initialize_session(path=session_path, workspace_root=workspace_root)

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
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="Z" * 20_000)]),
            ModelRequest(parts=[RetryPromptPart(content="retry with more detail")]),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )

    original_tail_budget = (
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS
    )
    try:
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS = 100
        await _append_auto_compaction_summary(
            path=session_path,
            workspace_root=workspace_root,
        )
    finally:
        session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS = (
            original_tail_budget
        )

    observed: dict[str, object] = {}

    async def probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["incoming_shapes"] = _message_shapes(messages)
        observed["incoming_user_prompts"] = _user_prompts(messages)
        yield "done"

    await _serve_lines(
        model=FunctionModel(stream_function=probe_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        lines=[
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "after-unsafe",
                },
            }
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.first_kept_run_id is None
    assert _message_shapes(loaded.latest_compaction.checkpoint_messages) == []
    assert observed["incoming_user_prompts"] == ["after-unsafe"]
    resume_instructions = build_resume_instructions(loaded)
    assert resume_instructions is not None
    assert "Current objective: continue after compaction" in resume_instructions
    assert observed["incoming_shapes"] == ["ModelRequest:['UserPromptPart']"]
    assert _user_prompts(loaded.runs[-1].messages) == ["after-unsafe"]


async def test_e2e_stdio_repeated_compactions_do_not_repersist_checkpoint_history(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "4" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    initialize_session(path=session_path, workspace_root=workspace_root)

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="A" * 120_000,
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="A" * 120_000)])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    for run_id, prompt in [("run-2", "keep-2"), ("run-3", "keep-3")]:
        append_run_to_session(
            path=session_path,
            workspace_root=workspace_root,
            prompt=prompt,
            thinking=None,
            messages=[ModelRequest(parts=[UserPromptPart(content=prompt)])],
            events=[
                RunStartedEvent(run_id=run_id),
                RunSucceededEvent(run_id=run_id, output_text="done"),
            ],
        )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return await _append_auto_compaction_summary(
            path=path,
            workspace_root=workspace_root,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.build_auto_compact_session_budget_report",
        lambda loaded_session, *, model: (
            trigger_module.build_auto_compact_session_budget_report(
                loaded_session,
                model=model,
                get_context_window_tokens=lambda _model: 2_000,
            )
        ),
    )

    messages = await _serve_lines(
        model=FunctionModel(stream_function=_text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        lines=[
            {
                "id": "req-run-1",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "B" * 120_000,
                },
            },
            {
                "id": "req-run-2",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "after-second-compaction",
                },
            },
        ],
    )

    event_types = [
        message["event"]["type"]
        for message in messages
        if message["type"] == "rpc_event"
    ]
    assert event_types.count("session_compaction_completed") == 2

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert len(loaded.compactions) == 2
    assert _user_prompts(loaded.runs[3].messages) == ["B" * 120_000]
    assert _user_prompts(loaded.runs[4].messages) == ["after-second-compaction"]
    assert _user_prompts(build_resume_message_history(loaded)) == [
        "after-second-compaction"
    ]


async def test_e2e_repeated_in_run_compaction_restores_raw_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    def _content(prefix: str) -> str:
        return "\n".join(
            f"{prefix}-{index:04d} abcdefghijklmnopqrstuvwxyz"
            for index in range(80)
        ) + "\n"

    contents = {
        "one.txt": _content("one"),
        "two.txt": _content("two"),
        "three.txt": _content("three"),
    }
    for path, content in contents.items():
        (workspace_root / path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.compaction.history_processors."
        "build_in_run_compaction_soft_char_limit",
        lambda _model: 400,
    )

    observed_rounds: list[list[str]] = []
    call_count = 0

    async def repeated_live_compaction_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        read_returns = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, ToolReturnPart) and part.tool_name == "read"
        ]
        observed_rounds.append(list(read_returns))

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "one.txt"}',
                    tool_call_id="call-one",
                )
            }
            return

        if call_count == 2:
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "two.txt"}',
                    tool_call_id="call-two",
                )
            }
            return

        if call_count == 3:
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "three.txt"}',
                    tool_call_id="call-three",
                )
            }
            return

        if call_count == 4:
            yield "done"
            return

        raise AssertionError(f"unexpected call_count: {call_count}")

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=repeated_live_compaction_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="inspect many files",
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
    assert len(observed_rounds) == 4
    assert observed_rounds[0] == []
    assert len(observed_rounds[1]) == 1
    assert observed_rounds[1][0].startswith(
        "Compacted historical read result for one.txt"
    )
    assert len(observed_rounds[2]) == 2
    assert observed_rounds[2][0].startswith(
        "Compacted historical read result for one.txt"
    )
    assert len(observed_rounds[3]) == 3
    assert observed_rounds[3][0].startswith(
        "Compacted historical read result for one.txt"
    )
    assert observed_rounds[3][1].startswith(
        "Compacted historical read result for two.txt"
    )
    assert observed_rounds[3][2].startswith(
        "Compacted historical read result for three.txt"
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    persisted_read_returns = [
        part
        for part in _all_parts(loaded.runs[0].messages)
        if isinstance(part, ToolReturnPart) and part.tool_name == "read"
    ]
    assert [part.tool_call_id for part in persisted_read_returns] == [
        "call-one",
        "call-two",
        "call-three",
    ]
    assert [part.content for part in persisted_read_returns] == [
        contents["one.txt"],
        contents["two.txt"],
        contents["three.txt"],
    ]
    assert all(
        not isinstance(part.metadata, dict)
        or IN_RUN_COMPACTION_METADATA_KEY not in part.metadata
        for part in persisted_read_returns
    )


async def test_e2e_in_run_compaction_restore_state_failure_fails_hard(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    big_content = "\n".join(
        f"line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    ) + "\n"

    controller = build_in_run_compaction_controller(soft_char_limit=120)
    compacted_messages = await controller.apply(
        [
            ModelRequest(parts=[UserPromptPart(content="inspect big file")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "big.txt"},
                        tool_call_id="call-read",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        content=big_content,
                        tool_call_id="call-read",
                    )
                ]
            ),
        ]
    )

    @contextmanager
    def fake_capture_run_messages():
        yield compacted_messages

    def broken_restore(messages: list[ModelMessage]) -> list[ModelMessage]:
        controller._original_content_by_storage_key.clear()
        return controller.restore(messages)

    monkeypatch.setattr(
        runtime_session_module,
        "capture_run_messages",
        fake_capture_run_messages,
    )
    async def finalizing_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        del agent, prompt, message_history, instructions, thinking, deps
        yield RunStartedEvent(run_id="run-1")
        yield RunSucceededEvent(run_id="run-1", output_text="done")

    monkeypatch.setattr(
        runtime_session_module,
        "build_compaction_history_runtime",
        lambda *, model: CompactionHistoryRuntime(
            history_processors=[],
            restore_messages=broken_restore,
        ),
    )
    monkeypatch.setattr(
        runtime_session_module,
        "stream_run_events",
        finalizing_stream_run_events,
    )

    with pytest.raises(
        RuntimeError,
        match="In-run compaction original content is missing",
    ):
        [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=_text_only_stream),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="go",
            )
        ]

    with pytest.raises(SessionFormatError, match="incomplete run"):
        load_session(path=session_path, workspace_root=workspace_root)


async def test_e2e_cancelled_run_after_live_compaction_restores_raw_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    started = asyncio.Event()
    big_content = "\n".join(
        f"line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    ) + "\n"

    controller = build_in_run_compaction_controller(soft_char_limit=120)
    compacted_messages = await controller.apply(
        [
            ModelRequest(parts=[UserPromptPart(content="go")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "README.md"},
                        tool_call_id="call-read",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        content=big_content,
                        tool_call_id="call-read",
                    )
                ]
            ),
        ]
    )

    @contextmanager
    def fake_capture_run_messages():
        yield compacted_messages

    async def cancellable_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        del (
            agent,
            prompt,
            message_history,
            instructions,
            thinking,
            deps,
            message_history_sink,
        )
        yield RunStartedEvent(run_id="run-1")
        yield ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-read",
            tool_name="read",
            args={"path": "README.md"},
            args_valid=True,
        )
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        runtime_session_module,
        "capture_run_messages",
        fake_capture_run_messages,
    )
    monkeypatch.setattr(
        runtime_session_module,
        "build_compaction_history_runtime",
        lambda *, model: CompactionHistoryRuntime(
            history_processors=[],
            restore_messages=controller.restore,
        ),
    )
    monkeypatch.setattr(
        runtime_session_module,
        "stream_run_events",
        cancellable_stream_run_events,
    )

    async def consume() -> list[object]:
        return [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=_text_only_stream),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="go",
            )
        ]

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    persisted_read_returns = [
        part
        for part in _all_parts(loaded.message_history)
        if isinstance(part, ToolReturnPart) and part.tool_name == "read"
    ]
    assert len(persisted_read_returns) == 1
    assert persisted_read_returns[0].content == big_content
    assert persisted_read_returns[0].metadata is None

    events = loaded.runs[0].events
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallFailedEvent)
    assert isinstance(events[3], RunFailedEvent)
    assert events[3].error_type == "CancelledError"
