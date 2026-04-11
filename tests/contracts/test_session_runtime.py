import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import date

import pytest
from pydantic_ai.messages import (
    BinaryContent,
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
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

import just_another_coding_agent.runtime.session as runtime_session_module
from just_another_coding_agent.contracts.compaction import CompactionBudgetReport
from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    ReadActivityDetails,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    SessionTurnContextStatusEvent,
    ToolActivity,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import SessionTurnContextEntry
from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.runtime.compaction import (
    build_resume_message_history,
    summarize_session_for_compaction,
)
from just_another_coding_agent.runtime.compaction import (
    session_summary as session_summary_module,
)
from just_another_coding_agent.runtime.compaction import (
    trigger as trigger_module,
)
from just_another_coding_agent.runtime.project_docs import (
    PROJECT_DOC_MESSAGE_HEADER,
)
from just_another_coding_agent.runtime.turn_context import (
    RUNTIME_CONTEXT_MESSAGE_HEADER,
    RUNTIME_CONTEXT_UPDATE_MESSAGE_HEADER,
    build_runtime_context_injection_plan,
    build_runtime_context_message,
    build_runtime_context_text,
    build_runtime_context_update_message,
    build_runtime_context_update_text,
    build_session_turn_context_entry,
    evaluate_turn_context_baseline,
)
from just_another_coding_agent.session import (
    SessionFormatError,
    append_compaction_to_session,
    append_run_to_session,
    load_session,
    read_session_metadata,
    update_session_auto_compaction_failures,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_replacement_messages,
    build_compaction_summary_message,
    extract_compaction_summary_text,
    is_compaction_summary_message,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps

_SHELL_FAMILY = detect_default_shell_family()


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        yield from message.parts


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


def _persisted_event_types(events) -> list[str]:
    return [event.type for event in events if event.type != "assistant_text_delta"]


def _summary_message_content(summary_text: str) -> str:
    return build_compaction_summary_message(summary_text).parts[0].content


def _user_prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, UserPromptPart)
    ]


def _assistant_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, TextPart)
    ]


def _runtime_context_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        text
        for text in _assistant_texts(messages)
        if text.startswith(RUNTIME_CONTEXT_MESSAGE_HEADER)
    ]


def _runtime_context_update_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        text
        for text in _assistant_texts(messages)
        if text.startswith(RUNTIME_CONTEXT_UPDATE_MESSAGE_HEADER)
    ]


def _project_doc_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        text
        for text in _assistant_texts(messages)
        if text.startswith(PROJECT_DOC_MESSAGE_HEADER)
    ]


def _expected_runtime_context_message_content(
    *,
    model,
    workspace_root,
    current_date: date | None = None,
    shell_family: str | None = None,
    thinking=None,
    timezone: str | None = None,
) -> str:
    entry = build_session_turn_context_entry(
        run_id="expected-runtime-context",
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=timezone,
        thinking=thinking,
    )
    return build_runtime_context_message(entry.runtime_context_text).parts[0].content


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


def _append_replacement_compaction(
    *,
    path,
    workspace_root,
    summary_text: str,
    compacted_through_run_id: str | None = None,
    replacement_messages: list[ModelMessage] | None = None,
    token_budget: int | None = None,
):
    if replacement_messages is None:
        loaded = load_session(path=path, workspace_root=workspace_root)
        replacement_messages = build_compaction_replacement_messages(
            model="test:model",
            messages=build_resume_message_history(loaded),
            summary_text=summary_text,
            token_budget=(
                session_summary_module.SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS
                if token_budget is None
                else token_budget
            ),
        )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        compacted_through_run_id=compacted_through_run_id,
        replacement_messages=replacement_messages,
    )


def _force_small_context_window(monkeypatch, *, context_window_tokens: int = 2_000):
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
            get_context_window_tokens=lambda _model: context_window_tokens,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "build_auto_compact_session_budget_report",
        fake_budget_report,
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
    assert loaded.runs[0].thinking is None
    assert [event.type for event in loaded.runs[0].events] == _persisted_event_types(
        events
    )
    assert loaded.runs[0].messages
    assert all(
        not isinstance(part, SystemPromptPart)
        for message in loaded.runs[0].messages
        for part in message.parts
    )
    assert all(
        not isinstance(message, ModelRequest) or message.instructions is None
        for message in loaded.runs[0].messages
    )
    assert loaded.message_history == loaded.runs[0].messages


async def test_stream_session_run_events_rejects_mismatched_existing_workspace(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="first",
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


async def test_stream_session_run_events_resumes_session_created_on_other_shell_family(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        shell_family="posix",
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        turn_context=build_session_turn_context_entry(
            run_id="run-1",
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            shell_family="posix",
        ),
    )

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
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
        )
        captured["deps"] = deps
        yield RunStartedEvent(run_id="run-2")
        # Mirror real stream_run_events: fire the sink before the
        # terminal yield so session.py's finalization invariant holds.
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="second")])]
            )
        yield RunSucceededEvent(run_id="run-2", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.detect_default_shell_family",
        lambda: "powershell",
    )
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

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "cleared"
    assert status.reason == "shell_family_mismatch"
    assert status.persisted_run_id == "run-1"
    deps = captured["deps"]
    assert isinstance(deps, WorkspaceDeps)
    assert deps.shell_family == "powershell"

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.header.shell_family == "posix"
    assert [run.run_id for run in loaded.runs] == ["run-1", "run-2"]
    assert loaded.latest_turn_context is not None
    assert loaded.latest_turn_context.run_id == "run-2"
    assert loaded.latest_turn_context.shell_family == "powershell"


async def test_stream_session_run_events_passes_root_session_id_in_deps(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session-id-target.jsonl"
    captured: dict[str, object] = {}

    async def fake_stream_run_events(
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
        )
        captured["deps"] = deps
        yield RunStartedEvent(run_id="run-1")
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="done")])]
            )
        yield RunSucceededEvent(run_id="run-1", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        fake_stream_run_events,
    )

    _ = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="go",
        )
    ]

    deps = captured["deps"]
    assert isinstance(deps, WorkspaceDeps)
    assert deps.session_scope.session_id == session_path.stem
    assert deps.session_scope.run_id is None
    assert deps.session_scope.parent_session_id is None
    assert deps.session_scope.parent_run_id is None


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


async def test_stream_session_run_events_persists_turn_context_snapshot(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    _ = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="hello",
            thinking="high",
        )
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    assert loaded.latest_turn_context is not None
    assert loaded.latest_turn_context.run_id == loaded.runs[-1].run_id
    assert loaded.latest_turn_context.workspace_root == str(workspace_root.resolve())
    assert loaded.latest_turn_context.shell_family == loaded.header.shell_family
    assert loaded.latest_turn_context.thinking == "high"
    assert loaded.latest_turn_context.runtime_context_text
    assert (
        f"Current workspace root: {workspace_root.resolve()}"
        in loaded.latest_turn_context.runtime_context_text
    )


async def test_stream_session_run_events_reports_next_request_context_window_used(
    tmp_path, monkeypatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    def fake_budget_report(*args, **kwargs) -> CompactionBudgetReport:
        return CompactionBudgetReport(
            should_compact=False,
            reason="within_budget",
            context_window_tokens=200_000,
            effective_context_window_tokens=184_000,
            output_headroom_tokens=16_000,
            trigger_budget_tokens=128_800,
            prompt_reserve_tokens=24_000,
            estimation_method="chars_per_token_v1",
            estimated_runtime_context_tokens=2_000,
            estimated_resume_message_tokens=8_000,
            estimated_pre_run_tokens=14_000,
            estimated_post_compaction_headroom_tokens=170_000,
            runs_since_latest_compaction=1,
        )

    monkeypatch.setattr(
        runtime_session_module,
        "build_auto_compact_session_budget_report",
        fake_budget_report,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="hello",
        )
    ]

    terminal = events[-1]
    assert isinstance(terminal, RunSucceededEvent)
    assert terminal.next_request_context_window_used == 0.07


async def test_stream_session_run_events_injects_runtime_context_prefix_on_new_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    observed: dict[str, object] = {}

    async def probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["assistant_texts"] = _assistant_texts(messages)
        observed["user_prompts"] = _user_prompts(messages)
        yield "done"

    model = FunctionModel(stream_function=probe_stream)

    events = [
        event
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="hello",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert observed["user_prompts"] == ["hello"]
    assert observed["assistant_texts"] == [
        _expected_runtime_context_message_content(
            model=model,
            workspace_root=workspace_root,
        )
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert _runtime_context_texts(loaded.runs[0].messages) == []


async def test_stream_session_run_events_reports_missing_turn_context_baseline(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        shell_family=_SHELL_FAMILY,
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
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

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"
    assert status.persisted_run_id is None


async def test_stream_session_run_events_reports_reused_turn_context_baseline(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    model = FunctionModel(stream_function=text_only_stream)

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        shell_family=_SHELL_FAMILY,
        prompt="first",
        thinking="high",
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        turn_context=build_session_turn_context_entry(
            run_id="run-1",
            model=model,
            workspace_root=workspace_root,
            thinking="high",
        ),
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="second",
        )
    ]

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "reused"
    assert status.reason == "matched"
    assert status.persisted_run_id == "run-1"


async def test_stream_session_run_events_reports_cleared_turn_context_on_model_mismatch(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        shell_family="posix",
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        turn_context=SessionTurnContextEntry(
            run_id="run-1",
            model="different:model",
            thinking=None,
            workspace_root=str(workspace_root.resolve()),
            shell_family="posix",
            current_date="2026-04-03",
            runtime_context_text="stale runtime context",
        ),
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

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "cleared"
    assert status.reason == "model_mismatch"
    assert status.persisted_run_id == "run-1"


async def test_stream_session_run_events_emits_runtime_context_diff_on_shell_change(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    model = FunctionModel(stream_function=text_only_stream)

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        shell_family="posix",
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        turn_context=build_session_turn_context_entry(
            run_id="run-1",
            model=model,
            workspace_root=workspace_root,
            current_date=date.today(),
            shell_family="posix",
        ),
    )

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        del agent, prompt, instructions, thinking, deps
        captured["message_history"] = message_history
        yield RunStartedEvent(run_id="run-2")
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="done")])]
            )
        yield RunSucceededEvent(run_id="run-2", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.detect_default_shell_family",
        lambda: "powershell",
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        fake_stream_run_events,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="second",
        )
    ]

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "cleared"
    assert status.reason == "shell_family_mismatch"
    assert _runtime_context_texts(captured["message_history"]) == [
        _expected_runtime_context_message_content(
            model=model,
            workspace_root=workspace_root,
            current_date=date.today(),
            shell_family="posix",
        )
    ]
    assert _runtime_context_update_texts(captured["message_history"]) == [
        build_runtime_context_update_message(
            "Current shell family changed to powershell"
        ).parts[0].content
    ]


async def test_stream_session_run_events_reports_missing_baseline_after_compaction(
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
        turn_context=build_session_turn_context_entry(
            run_id="run-1",
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
        ),
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        replacement_messages=[build_compaction_summary_message("Continue the task")],
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

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "no_active_turn_context"
    assert status.persisted_run_id is None


def test_evaluate_turn_context_baseline_clears_on_current_date_mismatch(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = FunctionModel(stream_function=text_only_stream)
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 2),
        thinking="high",
    )

    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 3),
        thinking="high",
        has_persisted_history=True,
    )

    assert decision.status == "cleared"
    assert decision.reason == "current_date_mismatch"
    assert decision.entry == entry


def test_build_runtime_context_injection_plan_uses_diff_for_date_change(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = FunctionModel(stream_function=text_only_stream)
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 3),
    )
    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        has_persisted_history=True,
    )

    plan = build_runtime_context_injection_plan(
        baseline_decision=decision,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
    )

    assert [message.parts[0].content for message in plan.before_history_messages] == [
        build_runtime_context_message(entry.runtime_context_text).parts[0].content
    ]
    assert [message.parts[0].content for message in plan.after_history_messages] == [
        build_runtime_context_update_message(
            build_runtime_context_update_text(
                entry=entry,
                model=model,
                workspace_root=workspace_root,
                current_date=date.today(),
            )
        ).parts[0].content
    ]


def test_build_runtime_context_injection_plan_uses_diff_for_model_change(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model="different:model",
        workspace_root=workspace_root,
        current_date=date.today(),
    )
    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        current_date=date.today(),
        has_persisted_history=True,
    )

    plan = build_runtime_context_injection_plan(
        baseline_decision=decision,
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        current_date=date.today(),
    )

    assert [message.parts[0].content for message in plan.before_history_messages] == [
        build_runtime_context_message(entry.runtime_context_text).parts[0].content
    ]
    assert [message.parts[0].content for message in plan.after_history_messages] == [
        build_runtime_context_update_message(
            build_runtime_context_update_text(
                entry=entry,
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                current_date=date.today(),
            )
        ).parts[0].content
    ]


def test_build_runtime_context_injection_plan_uses_diff_for_thinking_change(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = FunctionModel(stream_function=text_only_stream)
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        thinking="high",
    )
    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        thinking="low",
        has_persisted_history=True,
    )

    plan = build_runtime_context_injection_plan(
        baseline_decision=decision,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        thinking="low",
    )

    assert [message.parts[0].content for message in plan.before_history_messages] == [
        build_runtime_context_message(entry.runtime_context_text).parts[0].content
    ]
    assert [message.parts[0].content for message in plan.after_history_messages] == [
        build_runtime_context_update_message(
            build_runtime_context_update_text(
                entry=entry,
                model=model,
                workspace_root=workspace_root,
                current_date=date.today(),
                thinking="low",
            )
        ).parts[0].content
    ]


def test_build_runtime_context_injection_plan_uses_diff_for_timezone_change(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = FunctionModel(stream_function=text_only_stream)
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        timezone="America/Los_Angeles",
    )
    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        timezone="America/New_York",
        has_persisted_history=True,
    )

    plan = build_runtime_context_injection_plan(
        baseline_decision=decision,
        model=model,
        workspace_root=workspace_root,
        current_date=date.today(),
        timezone="America/New_York",
    )

    assert [message.parts[0].content for message in plan.before_history_messages] == [
        build_runtime_context_message(entry.runtime_context_text).parts[0].content
    ]
    assert [message.parts[0].content for message in plan.after_history_messages] == [
        build_runtime_context_update_message(
            build_runtime_context_update_text(
                entry=entry,
                model=model,
                workspace_root=workspace_root,
                current_date=date.today(),
                timezone="America/New_York",
            )
        ).parts[0].content
    ]


async def test_stream_session_run_events_persists_partial_run_before_completion(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    async def failing_stream_run_events(
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
            tool_call_id="call-write",
            tool_name="write",
            args={"path": "note.txt", "content": "hello\n"},
            args_valid=True,
        )
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        failing_stream_run_events,
    )

    with pytest.raises(RuntimeError, match="boom"):
        _ = [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=make_write_stream()),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="go",
            )
        ]

    line_types = [
        json.loads(line)["type"]
        for line in session_path.read_text(encoding="utf-8").splitlines()
    ]
    assert line_types == [
        "session_header",
        "session_run",
        "session_event",
        "session_event",
    ]

    with pytest.raises(SessionFormatError, match="Session ended with incomplete run"):
        load_session(path=session_path, workspace_root=workspace_root)


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
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        captured["prompt"] = prompt
        captured["instructions"] = instructions
        captured["thinking"] = thinking
        captured["message_history"] = message_history
        captured["deps"] = deps
        captured["message_history_sink"] = message_history_sink
        yield RunStartedEvent(run_id="run-2")
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="done")])]
            )
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

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"
    assert captured["prompt"] == "second"
    assert captured["thinking"] == "high"
    deps = captured["deps"]
    assert isinstance(deps, WorkspaceDeps)
    assert deps.workspace_root == workspace_root.resolve()
    assert deps.session_scope.session_id == session_path.stem
    assert deps.session_scope.run_id is None
    assert _project_doc_texts(captured["message_history"]) == []
    assert _runtime_context_texts(captured["message_history"]) == [
        _expected_runtime_context_message_content(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            thinking="high",
        )
    ]
    assert _user_prompts(captured["message_history"]) == ["first"]
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.thinking for run in loaded.runs] == ["high", "high"]
    assert loaded.thinking == "high"


async def test_stream_session_run_events_injects_workspace_project_docs(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").write_text(
        "Read docs/README.md first.\n",
        encoding="utf-8",
    )
    (workspace_root / "CLAUDE.md").write_text(
        "Prefer repo-grounded answers.\n",
        encoding="utf-8",
    )
    session_path = tmp_path / "session.jsonl"

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        captured["message_history"] = message_history
        yield RunStartedEvent(run_id="run-1")
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="done")])]
            )
        yield RunSucceededEvent(run_id="run-1", output_text="done")

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
            prompt="what is compaction?",
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert _project_doc_texts(captured["message_history"]) == [
        (
            f"{PROJECT_DOC_MESSAGE_HEADER} from AGENTS.md:\n\n"
            "<INSTRUCTIONS>\nRead docs/README.md first.\n\n</INSTRUCTIONS>"
        ),
        (
            f"{PROJECT_DOC_MESSAGE_HEADER} from CLAUDE.md:\n\n"
            "<INSTRUCTIONS>\nPrefer repo-grounded answers.\n\n</INSTRUCTIONS>"
        ),
    ]


def test_should_not_auto_compact_tiny_history_only_because_five_runs_exist(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for index in range(5):
        _append_simple_run(
            path=session_path,
            workspace_root=workspace_root,
            run_id=f"run-{index + 1}",
            prompt=f"small-{index + 1}",
        )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    report = trigger_module.build_auto_compact_session_budget_report(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 100_000,
    )

    assert report.should_compact is False
    assert report.reason == "within_budget"
    assert report.runs_since_latest_compaction == 5


def test_should_not_auto_compact_again_without_new_runs_after_latest_compaction(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    large_prompt = "x" * 400_000

    for index in range(2):
        _append_simple_run(
            path=session_path,
            workspace_root=workspace_root,
            run_id=f"run-{index + 1}",
            prompt=large_prompt,
        )

    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: continue",
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    report = trigger_module.build_auto_compact_session_budget_report(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 2_000,
    )

    assert report.should_compact is False
    assert report.reason == "no_new_work"
    assert report.runs_since_latest_compaction == 0


def test_should_auto_compact_again_after_new_large_run_post_compaction(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    large_prompt = "y" * 400_000

    for index in range(2):
        _append_simple_run(
            path=session_path,
            workspace_root=workspace_root,
            run_id=f"run-{index + 1}",
            prompt=large_prompt,
        )

    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: continue",
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-3",
        prompt=large_prompt,
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    report = trigger_module.build_auto_compact_session_budget_report(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 2_000,
    )

    assert report.should_compact is True
    assert report.reason == "over_budget"
    assert report.runs_since_latest_compaction == 1


async def test_stream_session_run_events_replays_replacement_history(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="first",
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="second",
    )
    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: continue after compaction",
        compacted_through_run_id="run-2",
        replacement_messages=[
            ModelRequest(parts=[UserPromptPart(content="second")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="write",
                        args={"path": "note.txt", "content": "hello\n"},
                        tool_call_id="call-write",
                    )
                ],
                model_name="test",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="write",
                        tool_call_id="call-write",
                        content="hello\n",
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
        observed["user_prompts"] = _user_prompts(messages)
        observed["assistant_texts"] = _assistant_texts(messages)
        observed["tool_return"] = _has_tool_return(messages, tool_name="write")
        yield "done"

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=probe_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="third",
        )
    ]

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"
    assert observed["user_prompts"] == ["second", "third"]
    assert observed["assistant_texts"] == [
        _expected_runtime_context_message_content(
            model=FunctionModel(stream_function=probe_stream),
            workspace_root=workspace_root,
        ),
        _summary_message_content("- Goal: continue after compaction")
    ]
    assert observed["tool_return"] is True

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert _user_prompts(loaded.runs[-1].messages) == ["third"]
    assert _user_prompts(build_resume_message_history(loaded)) == ["second", "third"]
    assert _summary_message_content("- Goal: continue after compaction") in (
        _assistant_texts(build_resume_message_history(loaded))
    )


def test_compaction_replacement_messages_preserve_user_prompt_text_sequence() -> None:
    replacement_messages = build_compaction_replacement_messages(
        model=TestModel(),
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "run go tests",
                            "  ",
                            "what is compaction?",
                        ]
                    )
                ]
            )
        ],
        summary_text="- Goal: continue",
        token_budget=100,
    )

    assert len(replacement_messages) == 2
    first_message = replacement_messages[0]
    assert isinstance(first_message, ModelRequest)
    assert len(first_message.parts) == 1
    first_part = first_message.parts[0]
    assert isinstance(first_part, UserPromptPart)
    assert first_part.content == "run go tests\nwhat is compaction?"
    assert is_compaction_summary_message(replacement_messages[-1])


def test_build_compaction_replacement_messages_rejects_non_text_user_content() -> None:
    with pytest.raises(
        ValueError,
        match="supports only text user prompt content",
    ):
        build_compaction_replacement_messages(
            model=TestModel(),
            messages=[
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=[
                                "describe this file",
                                BinaryContent(
                                    data=b"example",
                                    media_type="text/plain",
                                ),
                            ]
                        )
                    ]
                )
            ],
            summary_text="- Goal: continue",
            token_budget=100,
        )


async def test_summarize_session_for_compaction_uses_model_output_and_previous_summary(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="inspect plan",
    )
    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: repair verifier\n- Important path: docs/plan.md",
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="patch app",
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    async def summary_probe(
        messages: list[ModelMessage],
        agent_info: object,
    ):
        prompt = _last_user_prompt(messages)
        assert prompt is not None
        assert "Previous compaction summary:" in prompt
        assert "- Goal: repair verifier" in prompt
        assert "Run run-2" in prompt
        assert "Primary intent:" in prompt
        assert "- patch app" in prompt
        assert "Run run-1" not in prompt
        assert getattr(agent_info, "instructions") is not None
        instructions = getattr(agent_info, "instructions")
        assert "Primary Intent:" in instructions
        assert "Completed Work:" in instructions
        assert "Important Files/Paths:" in instructions
        assert "Do not include code snippets" in instructions
        yield (
            "- Goal: ship the verified fix\n"
            "- Important path: src/app.py"
        )

    summary = await summarize_session_for_compaction(
        model=FunctionModel(stream_function=summary_probe),
        loaded_session=loaded,
    )

    assert summary == "- Goal: ship the verified fix\n- Important path: src/app.py"


async def test_summarize_session_for_compaction_normalizes_summary_content(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="inspect plan",
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    summary = await summarize_session_for_compaction(
        model=TestModel(
            call_tools=[],
            custom_output_text="\n\n- Goal: ship it\n\n- Path: src/app.py\n\n",
        ),
        loaded_session=loaded,
    )

    assert summary == "- Goal: ship it\n- Path: src/app.py"


async def test_summarize_session_for_compaction_streams_with_canonical_model_settings(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="inspect plan",
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    captured: dict[str, object] = {}
    expected_settings = {"openai_store": False, "parallel_tool_calls": True}
    resolved_model = object()

    class FakeAgent:
        def __init__(self, model, output_type, instructions) -> None:
            captured["model"] = model
            captured["output_type"] = output_type
            captured["instructions"] = instructions

        def run_stream(self, prompt, *, model_settings=None):
            captured["prompt"] = prompt
            captured["model_settings"] = model_settings

            class StreamResult:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return None

                async def get_output(self):
                    return "- Goal: continue"

            return StreamResult()

    monkeypatch.setattr(session_summary_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        session_summary_module,
        "resolve_canonical_model",
        lambda model: resolved_model,
    )
    monkeypatch.setattr(
        session_summary_module,
        "build_canonical_model_settings",
        lambda *, model, thinking=None: expected_settings,
    )

    summary = await summarize_session_for_compaction(
        model="openai-responses:gpt-5.4-chatgpt",
        loaded_session=loaded,
    )

    assert summary == "- Goal: continue"
    assert captured["model"] is resolved_model
    assert captured["model_settings"] == expected_settings


async def test_summarize_session_for_compaction_rejects_empty_summary(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="inspect plan",
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    with pytest.raises(SessionFormatError, match="Compaction summary is empty"):
        await summarize_session_for_compaction(
            model=TestModel(call_tools=[], custom_output_text=" \n \n"),
            loaded_session=loaded,
        )


def test_session_compaction_source_trims_oldest_runs_and_uses_previous_summary(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="inspect plan",
    )
    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: repair verifier",
    )

    for index in range(2, 5):
        append_run_to_session(
            path=session_path,
            workspace_root=workspace_root,
            prompt=f"run {index}",
            thinking=None,
            messages=[ModelRequest(parts=[UserPromptPart(content=f"run {index}")])],
            events=[
                RunStartedEvent(run_id=f"run-{index}"),
                ToolCallStartedEvent(
                    run_id=f"run-{index}",
                    tool_call_id=f"call-read-{index}",
                    tool_name="read",
                    args={"path": f"file-{index}.py"},
                    args_valid=True,
                ),
                ToolCallSucceededEvent(
                    run_id=f"run-{index}",
                    tool_call_id=f"call-read-{index}",
                    tool_name="read",
                    result="read result",
                    activity=ToolActivity(
                        title=f"Read file-{index}.py",
                        details=ReadActivityDetails(
                            path=str(workspace_root / f"file-{index}.py"),
                            short_path=f"file-{index}.py",
                            offset=1,
                            limit=200,
                        ),
                    ),
                ),
                RunSucceededEvent(run_id=f"run-{index}", output_text="done"),
            ],
        )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    source = session_summary_module._build_bounded_compaction_source(
        loaded,
        max_chars=360,
    )

    assert "Previous compaction summary:" in source
    assert "- Goal: repair verifier" in source
    assert "Runs since the latest compaction boundary:" in source
    assert "Run run-4" in source
    assert "Primary intent:" in source
    assert "Current state:" in source
    assert "Completed work:" in source
    assert "Tool evidence:" in source
    assert "Read file-4.py" in source
    assert "omitted" in source
    assert "Run run-2" not in source


def test_session_compaction_source_fails_when_source_cannot_fit(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="x" * 400,
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    with pytest.raises(
        SessionFormatError,
        match="Compaction source does not fit within the active model context window",
    ):
        session_summary_module._build_bounded_compaction_source(
            loaded,
            max_chars=10,
        )


async def test_stream_session_run_events_auto_compacts_stale_session_before_resuming(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    large_prompt = "a" * 120_000

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt=large_prompt,
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="keep tail",
    )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return _append_replacement_compaction(
            path=path,
            workspace_root=workspace_root,
            summary_text="- Goal: continue after compaction",
        )

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
    ):
        del agent, thinking, deps
        captured["prompt"] = prompt
        captured["instructions"] = instructions
        captured["message_history"] = message_history
        if message_history_sink is not None:
            message_history_sink([ModelRequest(parts=[UserPromptPart(content=prompt)])])
        yield RunStartedEvent(run_id="run-3")
        yield RunSucceededEvent(run_id="run-3", output_text="done")

    _force_small_context_window(monkeypatch)
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
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
            prompt="follow-up",
        )
    ]

    assert [event.type for event in events] == [
        "session_compaction_started",
        "session_compaction_completed",
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    completed = events[1]
    status = events[2]
    assert completed.compacted_through_run_id == "run-2"
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"
    assert completed.budget_after.estimated_replacement_summary_tokens > 0
    assert completed.estimated_tokens_saved > 0
    assert completed.estimated_headroom_gain_tokens is not None
    assert completed.budget_after.estimated_post_compaction_headroom_tokens > (
        completed.budget_before.estimated_post_compaction_headroom_tokens
    )

    assert captured["prompt"] == "follow-up"
    assert captured["instructions"] is None
    assert _runtime_context_texts(captured["message_history"]) == [
        _expected_runtime_context_message_content(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
        )
    ]
    captured_prompts = _user_prompts(captured["message_history"])
    assert "keep tail" in captured_prompts
    assert _summary_message_content(
        "- Goal: continue after compaction"
    ) in _assistant_texts(captured["message_history"])

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert extract_compaction_summary_text(
        loaded.latest_compaction.replacement_messages
    ) == (
        "- Goal: continue after compaction"
    )
    assert _user_prompts(loaded.runs[-1].messages) == ["follow-up"]


async def test_stream_session_run_events_continues_after_repeated_auto_compaction(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    large_prompt = "b" * 120_000

    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt=large_prompt,
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-2",
        prompt="keep-2",
    )
    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: first compaction",
    )
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-3",
        prompt=large_prompt,
    )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return _append_replacement_compaction(
            path=path,
            workspace_root=workspace_root,
            summary_text="- Goal: second compaction",
        )

    async def fake_stream_run_events(
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
        if message_history_sink is not None:
            message_history_sink(
                [ModelRequest(parts=[UserPromptPart(content="after-second-compaction")])]
            )
        yield RunStartedEvent(run_id="run-4")
        yield RunSucceededEvent(run_id="run-4", output_text="done")

    _force_small_context_window(monkeypatch)
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
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
            prompt="after-second-compaction",
        )
    ]

    assert [event.type for event in events] == [
        "session_compaction_started",
        "session_compaction_completed",
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    assert isinstance(events[2], SessionTurnContextStatusEvent)
    assert events[2].status == "missing"
    assert events[2].reason == "missing"
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert len(loaded.compactions) == 2
    assert extract_compaction_summary_text(
        loaded.latest_compaction.replacement_messages
    ) == (
        "- Goal: second compaction"
    )


async def test_stream_session_run_events_records_auto_compaction_failures(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="c" * 120_000,
    )

    _force_small_context_window(monkeypatch)
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _ = [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="follow-up",
            )
        ]

    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.consecutive_auto_compaction_failures == 1


async def test_stream_session_run_events_resets_auto_compaction_failures_on_success(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="d" * 120_000,
    )
    update_session_auto_compaction_failures(
        path=session_path,
        consecutive_auto_compaction_failures=2,
    )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        return _append_replacement_compaction(
            path=path,
            workspace_root=workspace_root,
            summary_text="- Goal: continue",
        )

    async def fake_stream_run_events(
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
        if message_history_sink is not None:
            message_history_sink([ModelRequest(parts=[UserPromptPart(content="done")])])
        yield RunStartedEvent(run_id="run-2")
        yield RunSucceededEvent(run_id="run-2", output_text="done")

    _force_small_context_window(monkeypatch)
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        fake_stream_run_events,
    )

    _ = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="follow-up",
        )
    ]

    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.consecutive_auto_compaction_failures == 0


async def test_stream_session_run_events_blocks_after_repeated_auto_compaction_failures(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    _append_simple_run(
        path=session_path,
        workspace_root=workspace_root,
        run_id="run-1",
        prompt="e" * 120_000,
    )
    update_session_auto_compaction_failures(
        path=session_path,
        consecutive_auto_compaction_failures=(
            runtime_session_module.MAX_CONSECUTIVE_AUTO_COMPACTION_FAILURES
        ),
    )
    _force_small_context_window(monkeypatch)

    with pytest.raises(RuntimeError, match="Auto-compaction blocked"):
        _ = [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="follow-up",
            )
        ]

    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.consecutive_auto_compaction_failures == (
        runtime_session_module.MAX_CONSECUTIVE_AUTO_COMPACTION_FAILURES
    )


async def test_stream_session_run_events_does_not_recompact_without_new_completed_run(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    large_prompt = "q" * 400_000

    for index in range(2):
        _append_simple_run(
            path=session_path,
            workspace_root=workspace_root,
            run_id=f"run-{index + 1}",
            prompt=large_prompt,
        )

    _append_replacement_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text="- Goal: continue the task",
    )

    async def fail_if_recompacted(**_kwargs):
        raise AssertionError("already compacted session should not compact again")

    async def fake_stream_run_events(
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
        if message_history_sink is not None:
            message_history_sink([ModelRequest(parts=[UserPromptPart(content="follow-up")])])
        yield RunStartedEvent(run_id="run-3")
        yield RunSucceededEvent(run_id="run-3", output_text="done")

    _force_small_context_window(monkeypatch)
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session."
        "summarize_and_append_compaction_to_session",
        fail_if_recompacted,
    )
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
            prompt="follow-up",
        )
    ]

    assert [event.type for event in events] == [
        "session_turn_context_status",
        "run_started",
        "run_succeeded",
    ]
    status = events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "missing"
    assert status.reason == "missing"


async def test_stream_session_run_events_persists_incomplete_partial_consumption(
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

    assert session_path.exists()
    assert [
        json.loads(line)["type"]
        for line in session_path.read_text(encoding="utf-8").splitlines()
    ] == ["session_header", "session_run", "session_event"]

    with pytest.raises(SessionFormatError, match="Session ended with incomplete run"):
        load_session(path=session_path, workspace_root=workspace_root)


async def test_stream_session_run_events_finalizes_cancelled_run(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    started = asyncio.Event()

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
        )
        try:
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
        finally:
            # Mirror real stream_run_events: fire the sink on cancellation
            # so session.py's finalize invariant holds.
            if message_history_sink is not None:
                message_history_sink(
                    [ModelRequest(parts=[UserPromptPart(content="go")])]
                )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        cancellable_stream_run_events,
    )

    async def consume() -> list[object]:
        return [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=text_only_stream),
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
    events = loaded.runs[0].events
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[1], ToolCallStartedEvent)
    assert isinstance(events[2], ToolCallFailedEvent)
    assert isinstance(events[3], RunFailedEvent)
    assert events[2].tool_call_id == "call-read"
    assert events[3].error_type == "CancelledError"


async def test_stream_session_run_events_yields_cancelled_run_failed_event(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    started = asyncio.Event()
    yielded: list[object] = []

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
        )
        try:
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
        finally:
            if message_history_sink is not None:
                message_history_sink(
                    [ModelRequest(parts=[UserPromptPart(content="go")])]
                )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        cancellable_stream_run_events,
    )

    async def consume() -> None:
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="go",
        ):
            yielded.append(event)

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert isinstance(yielded[0], RunStartedEvent)
    assert isinstance(yielded[1], ToolCallStartedEvent)
    assert isinstance(yielded[2], ToolCallFailedEvent)
    assert isinstance(yielded[3], RunFailedEvent)
    assert yielded[3].error_type == "CancelledError"


async def test_stream_session_run_events_sanitize_cancelled_run_messages(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    started = asyncio.Event()

    partial_messages = [
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
    ]

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
        )
        try:
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
        finally:
            # Mirror real stream_run_events: on any non-success termination
            # (including cancellation) publish whatever messages pydantic-ai
            # has accumulated so far via the sink, so the outer
            # stream_session_run_events can persist and sanitize them.
            if message_history_sink is not None:
                message_history_sink(partial_messages)

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        cancellable_stream_run_events,
    )

    async def consume() -> list[object]:
        return [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=text_only_stream),
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
    assert [
        part.tool_call_id
        for part in _all_parts(loaded.message_history)
        if isinstance(part, ToolCallPart)
    ] == []


async def test_stream_session_run_events_trim_failed_correction_tail_from_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    poisoned_messages = [
        ModelRequest(parts=[UserPromptPart(content="get familiar with repo!")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="readread",
                    args='{"path":"README.md"}{"path":"AGENTS.md"}',
                    tool_call_id="call-readread",
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    "Unknown tool name: 'readread'. Available tools: 'read'",
                    tool_name="readread",
                    tool_call_id="call-readread",
                )
            ]
        ),
    ]
    captured_messages = [
        build_runtime_context_message(
            build_runtime_context_text(workspace_root=workspace_root)
        ),
        *poisoned_messages,
    ]

    async def failed_correction_stream_run_events(
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
        )
        yield RunStartedEvent(run_id="run-1")
        yield ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-readread",
            tool_name="readread",
            args=None,
            args_valid=False,
        )
        yield ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-readread",
            tool_name="readread",
            result={
                "ok": False,
                "error_type": "RetryPromptPart",
                "message": "Unknown tool name: 'readread'. Available tools: 'read'",
            },
        )
        # Mirror real stream_run_events: publish messages via the sink
        # before emitting the terminal RunFailedEvent so the outer
        # stream_session_run_events can sanitize and persist them.
        if message_history_sink is not None:
            message_history_sink(poisoned_messages)
        yield RunFailedEvent(
            run_id="run-1",
            error_type="ModelHTTPError",
            message="status_code: 400, invalid tool call arguments",
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        failed_correction_stream_run_events,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="get familiar with repo!",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "run_failed",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [type(part).__name__ for part in _all_parts(loaded.message_history)] == [
        "UserPromptPart"
    ]
    assert _last_user_prompt(loaded.message_history) == "get familiar with repo!"
    assert [
        part.tool_call_id
        for part in _all_parts(loaded.message_history)
        if isinstance(part, ToolCallPart)
    ] == []
    assert [
        part.tool_call_id
        for part in _all_parts(loaded.message_history)
        if isinstance(part, RetryPromptPart)
    ] == []
    assert [event.type for event in loaded.runs[0].events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "run_failed",
    ]


async def test_stream_session_run_events_resume_after_failed_correction_is_clean(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    poisoned_messages = [
        ModelRequest(parts=[UserPromptPart(content="get familiar with repo!")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="readread",
                    args='{"path":"README.md"}{"path":"AGENTS.md"}',
                    tool_call_id="call-readread",
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    "Unknown tool name: 'readread'. Available tools: 'read'",
                    tool_name="readread",
                    tool_call_id="call-readread",
                )
            ]
        ),
    ]
    captured_messages = [
        build_runtime_context_message(
            build_runtime_context_text(workspace_root=workspace_root)
        ),
        *poisoned_messages,
    ]

    async def failed_correction_stream_run_events(
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
        )
        yield RunStartedEvent(run_id="run-1")
        yield ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-readread",
            tool_name="readread",
            args=None,
            args_valid=False,
        )
        yield ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-readread",
            tool_name="readread",
            result={
                "ok": False,
                "error_type": "RetryPromptPart",
                "message": "Unknown tool name: 'readread'. Available tools: 'read'",
            },
        )
        if message_history_sink is not None:
            message_history_sink(poisoned_messages)
        yield RunFailedEvent(
            run_id="run-1",
            error_type="ModelHTTPError",
            message="status_code: 400, invalid tool call arguments",
        )

    original_stream_run_events = runtime_session_module.stream_run_events

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        failed_correction_stream_run_events,
    )

    _ = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="get familiar with repo!",
        )
    ]

    probe_observed: dict[str, list[str]] = {}

    async def clean_resume_probe_stream(messages, _agent_info):
        probe_observed["part_types"] = [
            type(part).__name__ for part in _all_parts(messages)
        ]
        probe_observed["user_prompts"] = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, UserPromptPart)
        ]
        assert not any(
            isinstance(part, ToolCallPart) and part.tool_name == "readread"
            for part in _all_parts(messages)
        )
        assert not any(
            isinstance(part, RetryPromptPart) for part in _all_parts(messages)
        )
        yield "clean"

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.stream_run_events",
        original_stream_run_events,
    )

    resumed_events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=clean_resume_probe_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="hello",
        )
    ]

    assert [event.type for event in resumed_events] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    status = resumed_events[0]
    assert isinstance(status, SessionTurnContextStatusEvent)
    assert status.status == "cleared"
    assert status.reason == "model_mismatch"
    assert status.persisted_run_id == "run-1"
    assert probe_observed["user_prompts"] == ["get familiar with repo!", "hello"]
