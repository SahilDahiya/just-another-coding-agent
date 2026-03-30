import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import SessionCompactionSummary
from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.runtime.compaction import (
    build_resume_message_history,
    summarize_session_for_compaction,
)
from just_another_coding_agent.runtime.compaction import (
    session_summary as session_summary_module,
)
from just_another_coding_agent.session import (
    SessionFormatError,
    append_compaction_to_session,
    append_run_to_session,
    load_session,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps


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


def make_deferred_bash_stream():
    call_count = 0

    async def deferred_bash_stream(_messages, _agent_info):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="shell",
                    json_args='{"command": "printf ok", "defer": true}',
                    tool_call_id="call-bash",
                )
            }
            return

        yield "done"

    return deferred_bash_stream


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


def _test_summary_model(*, custom_output_args: dict[str, object]) -> TestModel:
    return TestModel(call_tools=[], custom_output_args=custom_output_args)


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


async def compacted_real_persisted_history_probe_stream(
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
        raise AssertionError(
            "raw summarized history should not be replayed"
        )
    if "second" not in all_user_prompts:
        raise AssertionError("current prompt should be present")
    if not any(
        prompt.startswith("Session compaction summary:")
        for prompt in system_prompts
    ):
        raise AssertionError("compaction summary should be injected")

    yield "done"


def make_live_compaction_probe_stream(observed: dict[str, object]):
    call_count = 0

    async def live_compaction_probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "big.txt"}',
                    tool_call_id="call-read",
                )
            }
            return

        tool_returns = [
            part
            for part in _all_parts(messages)
            if isinstance(part, ToolReturnPart) and part.tool_name == "read"
        ]
        assert len(tool_returns) == 1
        observed["compacted_tool_return"] = tool_returns[0].content
        yield "done"

    return live_compaction_probe_stream


def make_resumed_live_compaction_probe_stream():
    call_count = 0

    async def resumed_live_compaction_probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1

        prompts = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, UserPromptPart)
        ]
        system_prompts = _system_prompt_contents(messages)
        read_returns = [
            part
            for part in _all_parts(messages)
            if isinstance(part, ToolReturnPart) and part.tool_name == "read"
        ]

        assert "summarized-first" not in prompts
        assert "retained-second" in prompts
        assert "inspect current big file" in prompts
        assert any(
            prompt.startswith("Session compaction summary:")
            for prompt in system_prompts
        )

        if call_count == 1:
            assert len(read_returns) == 1
            retained_read = read_returns[0].content
            assert isinstance(retained_read, str)
            assert retained_read.startswith(
                "retained-0000 abcdefghijklmnopqrstuvwxyz"
            )
            yield {
                0: DeltaToolCall(
                    name="read",
                    json_args='{"path": "current-big.txt"}',
                    tool_call_id="call-current-read",
                )
            }
            return

        assert len(read_returns) == 2
        compacted_retained_read = read_returns[0].content
        compacted_current_read = read_returns[1].content
        assert isinstance(compacted_retained_read, str)
        assert isinstance(compacted_current_read, str)
        assert compacted_retained_read.startswith(
            "Compacted historical read result for retained-big.txt"
        )
        assert "80 lines" in compacted_retained_read
        assert compacted_current_read.startswith(
            "Compacted historical read result for current-big.txt"
        )
        assert "80 lines" in compacted_current_read
        yield "done"

    return resumed_live_compaction_probe_stream


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


async def test_stream_session_run_events_persists_deferred_bash_tool_return(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=make_deferred_bash_stream()),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="go",
            tool_names=("shell",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert _has_tool_return(loaded.message_history, tool_name="shell")


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
    )

    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        thinking=None,
        deps=None,
        enable_server_history=False,
        message_history_sink=None,
    ):
        del (
            agent,
            prompt,
            message_history,
            thinking,
            enable_server_history,
            message_history_sink,
        )
        captured["deps"] = deps
        yield RunStartedEvent(run_id="run-2")
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

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    deps = captured["deps"]
    assert isinstance(deps, WorkspaceDeps)
    assert deps.shell_family == "powershell"

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.header.shell_family == "posix"
    assert [run.run_id for run in loaded.runs] == ["run-1", "run-2"]


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
        thinking=None,
        deps=None,
        enable_server_history=False,
        message_history_sink=None,
    ):
        del (
            agent,
            prompt,
            message_history,
            thinking,
            deps,
            enable_server_history,
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

    with pytest.raises(
        SessionFormatError,
        match="Session ended with incomplete run",
    ):
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
        thinking=None,
        deps=None,
        enable_server_history=False,
        message_history_sink=None,
    ):
        captured["prompt"] = prompt
        captured["thinking"] = thinking
        captured["message_history"] = message_history
        captured["deps"] = deps
        captured["enable_server_history"] = enable_server_history
        captured["message_history_sink"] = message_history_sink
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
    assert captured["deps"] == WorkspaceDeps.from_workspace_root(workspace_root)
    assert captured["enable_server_history"] is True
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


async def test_stream_session_run_events_replays_compacted_history_after_real_run(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    first_events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="first",
        )
    ]
    assert [event.type for event in first_events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded_before_compaction = load_session(
        path=session_path,
        workspace_root=workspace_root,
    )
    assert loaded_before_compaction.runs[0].messages
    assert all(
        message.run_id is not None
        for message in loaded_before_compaction.runs[0].messages
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

    second_events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(
                stream_function=compacted_real_persisted_history_probe_stream
            ),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="second",
        )
    ]

    assert [event.type for event in second_events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]


def test_build_resume_message_history_uses_summary_plus_retained_runs(tmp_path) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

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
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="third",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="third")])],
        events=[
            RunStartedEvent(run_id="run-3"),
            RunSucceededEvent(run_id="run-3", output_text="done"),
        ],
    )

    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + json.dumps(
            {
                "type": "session_compaction",
                "compaction_id": "compact-1",
                "summarized_through_run_id": "run-1",
                "first_kept_run_id": "run-2",
                "summary": {
                    "current_objective": "continue from the retained runs",
                    "established_facts": ["first is summarized"],
                    "user_preferences": [],
                    "important_paths": [],
                    "open_questions": [],
                    "unresolved_work": ["finish the task"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    resume_history = build_resume_message_history(loaded)

    assert _system_prompt_contents(resume_history) == [
        "Session compaction summary:\n"
        "Current objective: continue from the retained runs\n"
        "Established facts:\n"
        "- first is summarized\n"
        "Unresolved work:\n"
        "- finish the task"
    ]
    assert [
        part.content
        for part in _all_parts(resume_history)
        if isinstance(part, UserPromptPart)
    ] == ["second", "third"]


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
    source = session_summary_module._build_bounded_compaction_source(
        loaded,
        max_chars=10_000,
    )
    assert "Run run-2" in source
    assert "Previous compaction summary:" in source
    assert "ship the first draft" in source
    assert "Run run-1" not in source

    summary = await summarize_session_for_compaction(
        model=_test_summary_model(
            custom_output_args={
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
        ),
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


async def test_summarize_session_for_compaction_normalizes_summary_content(
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

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    summary = await summarize_session_for_compaction(
        model=_test_summary_model(
            custom_output_args={
                "current_objective": "  finish the draft  ",
                "established_facts": [
                    "  The draft exists.  ",
                    "",
                    "The draft exists.",
                ],
                "user_preferences": [" concise ", "concise", " "],
                "important_paths": [" src/app.py ", "src/app.py"],
                "open_questions": ["  Should we ship?  ", ""],
                "unresolved_work": [" run tests ", "run tests"],
            }
        ),
        loaded_session=loaded,
    )

    assert summary.current_objective == "finish the draft"
    assert summary.established_facts == ["The draft exists."]
    assert summary.user_preferences == ["concise"]
    assert summary.important_paths == ["src/app.py"]
    assert summary.open_questions == ["Should we ship?"]
    assert summary.unresolved_work == ["run tests"]


async def test_summarize_session_for_compaction_rejects_empty_summary(
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

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    with pytest.raises(
        SessionFormatError,
        match="Compaction summary is empty",
    ):
        await summarize_session_for_compaction(
            model=_test_summary_model(
                custom_output_args={
                    "current_objective": None,
                    "established_facts": [],
                    "user_preferences": [],
                    "important_paths": [],
                    "open_questions": [],
                    "unresolved_work": [],
                }
            ),
            loaded_session=loaded,
        )


def test_session_compaction_source_trims_oldest_runs_and_uses_structured_source(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt=("first " * 80).strip(),
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="first")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="shell",
                        content="x" * 8_000,
                        tool_call_id="call-first-shell",
                    )
                ]
            ),
        ],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="old output"),
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
        messages=[
            ModelRequest(parts=[UserPromptPart(content="second")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="shell",
                        content="y" * 8_000,
                        tool_call_id="call-second-shell",
                    )
                ]
            ),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            ToolCallStartedEvent(
                run_id="run-2",
                tool_call_id="call-write",
                tool_name="write",
                args={"path": "note.txt", "content": "hello"},
                args_valid=True,
                activity={"title": "write note.txt"},
            ),
            ToolCallSucceededEvent(
                run_id="run-2",
                tool_call_id="call-write",
                tool_name="write",
                result="wrote file",
                activity={
                    "title": "write note.txt",
                    "summary": "wrote file",
                    "details": {
                        "kind": "write",
                        "path": "note.txt",
                        "bytes_written": 42,
                    },
                },
            ),
            RunSucceededEvent(run_id="run-2", output_text="latest output"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    source = session_summary_module._build_bounded_compaction_source(
        loaded,
        max_chars=450,
    )

    assert "Previous compaction summary:" in source
    assert "Run run-1" not in source
    assert "Run run-2" in source
    assert "write note.txt: wrote file" in source
    assert "tool_return shell:" not in source
    assert "Events:" not in source
    assert '"type":"run_succeeded"' not in source


def test_session_compaction_source_fails_when_source_cannot_fit(
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
            current_objective="x" * 500,
            established_facts=[],
            user_preferences=[],
            important_paths=[],
            open_questions=[],
            unresolved_work=[],
        ),
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    with pytest.raises(
        SessionFormatError,
        match="Compaction source does not fit within the active model context window",
    ):
        session_summary_module._build_bounded_compaction_source(
            loaded,
            max_chars=80,
        )


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

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        loaded = load_session(path=path, workspace_root=workspace_root)
        source = session_summary_module._build_bounded_compaction_source(
            loaded,
            max_chars=10_000,
        )
        assert "Run run-5" in source
        return append_compaction_to_session(
            path=path,
            workspace_root=workspace_root,
            summary=SessionCompactionSummary(
                current_objective="continue after auto compaction",
                established_facts=["Five runs were summarized."],
                user_preferences=[],
                important_paths=["note.txt"],
                open_questions=[],
                unresolved_work=["Handle the follow-up prompt."],
            ),
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
    try:
        events = [
            event
            async for event in stream_session_run_events(
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                session_path=session_path,
                prompt="follow-up",
            )
        ]
    finally:
        monkeypatch.undo()

    assert [event.type for event in events] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert len(loaded.compactions) == 1
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.summarized_through_run_id == "run-5"
    assert loaded.latest_compaction.first_kept_run_id is None
    assert (
        loaded.latest_compaction.summary.current_objective
        == "continue after auto compaction"
    )

async def test_live_compaction_preserves_raw_persisted_messages(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    big_lines = [f"line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)]
    big_content = "\n".join(big_lines) + "\n"
    (workspace_root / "big.txt").write_text(big_content, encoding="utf-8")

    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.agent."
        "build_in_run_compaction_soft_char_limit",
        lambda _model: 400,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(
                stream_function=make_live_compaction_probe_stream(observed)
            ),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="inspect the big file",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]

    compacted_tool_return = observed["compacted_tool_return"]
    assert isinstance(compacted_tool_return, str)
    assert compacted_tool_return.startswith("Compacted historical read result")
    assert "big.txt" in compacted_tool_return
    assert "80 lines" in compacted_tool_return
    assert "line-0000 abcdefghijklmnopqrstuvwxyz" not in compacted_tool_return

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    persisted_tool_returns = [
        part
        for part in _all_parts(loaded.runs[0].messages)
        if isinstance(part, ToolReturnPart) and part.tool_name == "read"
    ]
    assert len(persisted_tool_returns) == 1
    persisted_tool_return = persisted_tool_returns[0].content
    assert isinstance(persisted_tool_return, str)
    assert persisted_tool_return.startswith("line-0000 abcdefghijklmnopqrstuvwxyz")
    assert "Compacted historical read result" not in persisted_tool_return


async def test_resumed_compacted_session_still_applies_live_in_run_compaction(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    retained_lines = [
        f"retained-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    ]
    retained_content = "\n".join(retained_lines) + "\n"
    current_lines = [
        f"current-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    ]
    current_content = "\n".join(current_lines) + "\n"
    (workspace_root / "current-big.txt").write_text(current_content, encoding="utf-8")

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="summarized-first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="summarized-first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="retained-second",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="retained-second")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "retained-big.txt"},
                        tool_call_id="call-retained-read",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        content=retained_content,
                        tool_call_id="call-retained-read",
                    )
                ]
            ),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + json.dumps(
            {
                "type": "session_compaction",
                "compaction_id": "compact-1",
                "summarized_through_run_id": "run-1",
                "first_kept_run_id": "run-2",
                "summary": {
                    "current_objective": "continue from the retained run",
                    "established_facts": ["run-1 is summarized"],
                    "user_preferences": [],
                    "important_paths": ["retained-big.txt", "current-big.txt"],
                    "open_questions": [],
                    "unresolved_work": ["inspect the current big file"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(
                stream_function=make_resumed_live_compaction_probe_stream()
            ),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="inspect current big file",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]


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

    with pytest.raises(
        SessionFormatError,
        match="Session ended with incomplete run",
    ):
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
        thinking=None,
        deps=None,
        enable_server_history=False,
        message_history_sink=None,
    ):
        del (
            agent,
            prompt,
            message_history,
            thinking,
            deps,
            enable_server_history,
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
