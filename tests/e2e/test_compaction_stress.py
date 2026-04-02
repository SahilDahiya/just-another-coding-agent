import io
import json
from collections.abc import AsyncIterator

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.contracts.session import SessionCompactionSummary
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.runtime.compaction import (
    build_compaction_summary_message,
    build_resume_message_history,
)
from just_another_coding_agent.runtime.compaction import (
    session_summary as session_summary_module,
)
from just_another_coding_agent.runtime.compaction import trigger as trigger_module
from just_another_coding_agent.session import (
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


def _summary_prompt_present(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(part, SystemPromptPart)
        and part.content.startswith("Session compaction summary:")
        for part in _all_parts(messages)
    )


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
    checkpoint_messages = (
        None
        if target.checkpoint_messages is None
        else [build_compaction_summary_message(summary), *target.checkpoint_messages]
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
        summarized_through_run_id=target.summarized_through_run_id,
        first_kept_run_id=target.first_kept_run_id,
        checkpoint_messages=checkpoint_messages,
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
        observed["has_summary"] = _summary_prompt_present(messages)
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
        "ModelRequest:['SystemPromptPart']",
        "ModelResponse:['ToolCallPart']",
        "ModelRequest:['ToolReturnPart']",
        "ModelResponse:['TextPart']",
    ]
    assert observed["incoming_user_prompts"] == ["after-split-tail"]
    assert observed["has_summary"] is True
    assert observed["incoming_shapes"][:4] == [
        "ModelRequest:['SystemPromptPart']",
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
        observed["has_summary"] = _summary_prompt_present(messages)
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
    assert _message_shapes(loaded.latest_compaction.checkpoint_messages) == [
        "ModelRequest:['SystemPromptPart']"
    ]
    assert observed["incoming_user_prompts"] == ["after-unsafe"]
    assert observed["has_summary"] is True
    assert observed["incoming_shapes"] == [
        "ModelRequest:['SystemPromptPart', 'UserPromptPart']"
    ]
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
