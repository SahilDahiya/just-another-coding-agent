import io
import json
import random
from collections.abc import AsyncIterator

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
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
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.runtime.compaction import build_resume_message_history
from just_another_coding_agent.runtime.compaction import (
    trigger as trigger_module,
)
from just_another_coding_agent.runtime.turn_context import (
    build_runtime_context_message,
    build_runtime_context_text,
)
from just_another_coding_agent.session import (
    append_compaction_to_session,
    append_run_to_session,
    initialize_session,
    load_session,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_replacement_messages,
    build_compaction_summary_message,
    extract_compaction_summary_text,
)
from tests.session_test_helpers import _message_shapes, _user_prompts


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


def _append_simple_run(*, path, workspace_root, run_id: str, prompt: str) -> None:
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt=prompt,
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content=prompt)])],
        events=[
            RunStartedEvent(run_id=run_id),
            RunSucceededEvent(run_id=run_id, output_text="done"),
        ],
    )


def _append_auto_compaction_summary(
    *,
    path,
    workspace_root,
    summary_text: str,
    token_budget: int = 400,
):
    loaded = load_session(path=path, workspace_root=workspace_root)
    replacement_messages = build_compaction_replacement_messages(
        model="test:model",
        messages=build_resume_message_history(loaded),
        summary_text=summary_text,
        token_budget=token_budget,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=replacement_messages,
    )


def _assistant_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, TextPart)
    ]


def _random_compaction_text(rng: random.Random, prefix: str) -> str:
    return (
        prefix
        + "".join(rng.choice("abcdef0123456789 ") for _ in range(rng.randint(20, 220)))
    ).strip()


async def test_e2e_stdio_auto_compaction_keeps_recent_user_tail_and_summary_message(
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

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="A" * 120_000,
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="second",
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-3",
        prompt="third",
    )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return _append_auto_compaction_summary(
            path=path,
            workspace_root=workspace_root,
            summary_text="- Goal: continue after compaction",
            token_budget=400,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )

    def fake_budget_report(
        loaded_session,
        *,
        model,
        workspace_root=None,
        current_date=None,
        shell_family=None,
        thinking=None,
    ):
        return trigger_module.build_auto_compact_session_budget_report(
            loaded_session,
            model=model,
            workspace_root=workspace_root,
            current_date=current_date,
            shell_family=shell_family,
            thinking=thinking,
            get_context_window_tokens=lambda _model: 2_000,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.build_auto_compact_session_budget_report",
        fake_budget_report,
    )

    observed: dict[str, object] = {}

    async def probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["user_prompts"] = _user_prompts(messages)
        observed["assistant_texts"] = _assistant_texts(messages)
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
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    completed_event = next(
        message["event"]
        for message in messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "session_compaction_completed"
    )
    assert completed_event["estimated_tokens_saved"] > 0
    assert completed_event["estimated_percent_saved"] > 0
    assert completed_event["estimated_headroom_gain_tokens"] > 0
    assert completed_event["budget_after"]["estimated_replacement_summary_tokens"] > 0
    assert completed_event["budget_after"]["estimated_replacement_messages_tokens"] > 0

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.compacted_through_run_id == "run-3"
    assert (
        extract_compaction_summary_text(
            loaded.latest_compaction.replacement_messages
        )
        == "- Goal: continue after compaction"
    )
    assert "second" in observed["user_prompts"]
    assert "third" in observed["user_prompts"]
    assert build_runtime_context_message(
        build_runtime_context_text(workspace_root=workspace_root)
    ).parts[0].content in observed["assistant_texts"]
    assert build_compaction_summary_message(
        "- Goal: continue after compaction"
    ).parts[0].content in observed["assistant_texts"]
    assert observed["user_prompts"][-1] == "follow-up"
    assert _user_prompts(loaded.runs[-1].messages) == ["follow-up"]


async def test_e2e_stdio_resume_replays_custom_replacement_messages_raw(
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

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="first",
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        compacted_through_run_id="run-1",
        replacement_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "note.txt"},
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
            build_compaction_summary_message("- Goal: continue after compaction"),
        ],
    )

    observed: dict[str, object] = {}

    async def probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["incoming_shapes"] = _message_shapes(messages)
        observed["incoming_user_prompts"] = _user_prompts(messages)
        observed["incoming_assistant_texts"] = _assistant_texts(messages)
        yield "done"

    rpc_messages = await _serve_lines(
        model=FunctionModel(stream_function=probe_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        lines=[
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "after-manual-compaction",
                },
            }
        ],
    )

    assert [
        message["event"]["type"]
        for message in rpc_messages
        if message["type"] == "rpc_event"
    ] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert observed["incoming_shapes"][0] == "ModelResponse:['TextPart']"
    assert observed["incoming_shapes"][1] == "ModelResponse:['ToolCallPart']"
    assert observed["incoming_shapes"][2].startswith("ModelRequest:['ToolReturnPart'")
    assert observed["incoming_user_prompts"] == ["after-manual-compaction"]
    assert observed["incoming_assistant_texts"] == [
        build_runtime_context_message(
            build_runtime_context_text(workspace_root=workspace_root)
        ).parts[0].content,
        build_compaction_summary_message("- Goal: continue after compaction").parts[
            0
        ].content,
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert _user_prompts(loaded.runs[-1].messages) == ["after-manual-compaction"]


async def test_e2e_stdio_repeated_auto_compactions_keep_new_run_delta_only(
    tmp_path,
    monkeypatch,
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

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="A" * 120_000,
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="keep-2",
    )

    compaction_count = 0

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        nonlocal compaction_count
        compaction_count += 1
        return _append_auto_compaction_summary(
            path=path,
            workspace_root=workspace_root,
            summary_text=f"- Goal: compaction {compaction_count}",
            token_budget=200,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )

    def fake_budget_report(
        loaded_session,
        *,
        model,
        workspace_root=None,
        current_date=None,
        shell_family=None,
        thinking=None,
    ):
        return trigger_module.build_auto_compact_session_budget_report(
            loaded_session,
            model=model,
            workspace_root=workspace_root,
            current_date=current_date,
            shell_family=shell_family,
            thinking=thinking,
            get_context_window_tokens=lambda _model: 2_000,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.build_auto_compact_session_budget_report",
        fake_budget_report,
    )

    rpc_messages = await _serve_lines(
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

    completed_events = [
        message["event"]
        for message in rpc_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "session_compaction_completed"
    ]
    assert len(completed_events) == 2
    assert all(event["estimated_tokens_saved"] > 0 for event in completed_events)
    assert all(
        event["budget_after"]["estimated_post_compaction_headroom_tokens"]
        > event["budget_before"]["estimated_post_compaction_headroom_tokens"]
        for event in completed_events
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert len(loaded.compactions) == 2
    assert (
        extract_compaction_summary_text(
            loaded.latest_compaction.replacement_messages
        )
        == "- Goal: compaction 2"
    )
    assert _user_prompts(loaded.runs[-1].messages) == ["after-second-compaction"]


def test_seeded_compaction_chaos_preserves_resume_invariants(tmp_path) -> None:
    rng = random.Random(20260403)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    initialize_session(path=session_path, workspace_root=workspace_root)

    run_count = 0
    compaction_count = 0

    for _ in range(120):
        loaded = load_session(path=session_path, workspace_root=workspace_root)
        do_compact = bool(loaded.runs) and rng.random() < 0.45

        if not do_compact:
            run_count += 1
            run_id = f"run-{run_count}"
            prompt = _random_compaction_text(rng, f"user-{run_count}: ")
            user_parts = [UserPromptPart(content=prompt)]
            if rng.random() < 0.25:
                user_parts.append(
                    UserPromptPart(
                        content=_random_compaction_text(
                            rng,
                            f"extra-{run_count}: ",
                        )
                    )
                )
            messages: list[ModelMessage] = [ModelRequest(parts=user_parts)]
            if rng.random() < 0.7:
                messages.append(
                    ModelResponse(
                        parts=[
                            TextPart(
                                content=_random_compaction_text(
                                    rng,
                                    f"assistant-{run_count}: ",
                                )
                            )
                        ]
                    )
                )
            if rng.random() < 0.5:
                call_id = f"call-{run_count}"
                messages.append(
                    ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="shell",
                                args={"command": "echo hi"},
                                tool_call_id=call_id,
                            )
                        ],
                        model_name="test",
                    )
                )
                messages.append(
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name="shell",
                                content="ok",
                                tool_call_id=call_id,
                            )
                        ]
                    )
                )
            append_run_to_session(
                path=session_path,
                workspace_root=workspace_root,
                prompt=prompt,
                thinking=None,
                messages=messages,
                events=[
                    RunStartedEvent(run_id=run_id),
                    RunSucceededEvent(run_id=run_id, output_text="done"),
                ],
            )
            continue

        compaction_count += 1
        summary_text = "\n".join(
            [
                f"- chaos summary {compaction_count}",
                f"- latest run count {run_count}",
            ]
        )
        pre_resume = build_resume_message_history(loaded)
        replacement_messages = build_compaction_replacement_messages(
            model="test:model",
            messages=pre_resume,
            summary_text=summary_text,
            token_budget=rng.randint(1, 500),
        )
        entry = append_compaction_to_session(
            path=session_path,
            workspace_root=workspace_root,
            replacement_messages=replacement_messages,
        )

        loaded_after = load_session(path=session_path, workspace_root=workspace_root)
        latest = loaded_after.latest_compaction
        assert latest is not None
        assert latest.compaction_id == entry.compaction_id
        assert latest.compacted_through_run_id == entry.compacted_through_run_id
        assert (
            extract_compaction_summary_text(latest.replacement_messages)
            == summary_text
        )
        assert latest.replacement_messages[-1].parts[0].content == (
            build_compaction_summary_message(summary_text).parts[0].content
        )
        assert sum(
            1
            for message in latest.replacement_messages
            if extract_compaction_summary_text([message]) is not None
        ) == 1

        resumed = build_resume_message_history(loaded_after)
        assert resumed == build_resume_message_history(loaded_after)

        compacted_run_index = next(
            index
            for index, run in enumerate(loaded_after.runs)
            if run.run_id == latest.compacted_through_run_id
        )
        later_messages = [
            message
            for run in loaded_after.runs[compacted_run_index + 1 :]
            for message in run.messages
        ]
        assert (
            resumed[: len(latest.replacement_messages)]
            == latest.replacement_messages
        )
        if later_messages:
            assert resumed[-len(later_messages) :] == later_messages

    assert run_count > 0
    assert compaction_count > 0
