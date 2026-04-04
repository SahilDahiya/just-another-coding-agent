import json

import pytest
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolActivity,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.contracts.session import (
    SESSION_FORMAT_VERSION,
    SessionTurnContextEntry,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.session import build_session_preview
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    append_run_to_session,
    append_session_name_to_session,
    fork_session,
    initialize_session,
    load_session,
    read_session_metadata,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_summary_message,
)
from tests.session_test_helpers import _compaction_entry_payload


def _persisted_events(events):
    return [
        event for event in events if not isinstance(event, AssistantTextDeltaEvent)
    ]


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
        thinking="high",
        events=events,
        messages=messages,
    )
    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.header.version == SESSION_FORMAT_VERSION
    assert loaded.header.workspace_root == str(workspace_root.resolve())
    assert len(loaded.runs) == 1
    assert loaded.runs[0].run_id == events[0].run_id
    assert loaded.runs[0].prompt == "go"
    assert loaded.runs[0].thinking == "high"
    assert loaded.runs[0].messages == messages
    assert loaded.runs[0].events == _persisted_events(events)
    assert loaded.message_history == messages
    assert loaded.thinking == "high"
    assert loaded.latest_turn_context is None


def test_append_run_persists_turn_context_snapshot(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    turn_context = SessionTurnContextEntry(
        run_id="run-1",
        model="openai-responses:gpt-5.3-codex",
        thinking="high",
        workspace_root=str(workspace_root.resolve()),
        shell_family="posix",
        current_date="2026-04-04",
        runtime_context_text="Current workspace root: /workspace",
    )

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking="high",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
        turn_context=turn_context,
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.turn_contexts == [turn_context]
    assert loaded.latest_turn_context == turn_context


def test_load_session_compaction_invalidates_latest_turn_context(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    turn_context = SessionTurnContextEntry(
        run_id="run-1",
        model="openai-responses:gpt-5.3-codex",
        thinking="high",
        workspace_root=str(workspace_root.resolve()),
        shell_family="posix",
        current_date="2026-04-04",
        runtime_context_text="Current workspace root: /workspace",
    )

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking="high",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
        turn_context=turn_context,
    )
    append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=[build_compaction_summary_message("Continue the task")],
        compacted_through_run_id="run-1",
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.turn_contexts == [turn_context]
    assert loaded.latest_turn_context is None


def test_build_session_preview_uses_recent_runs_only(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    initialize_session(path=path, workspace_root=workspace_root)

    for index in range(1, 13):
        run_id = f"run-{index}"
        prompt = f"prompt {index}"
        output = f"answer {index}"
        append_run_to_session(
            path=path,
            workspace_root=workspace_root,
            prompt=prompt,
            events=[
                RunStartedEvent(run_id=run_id),
                RunSucceededEvent(run_id=run_id, output_text=output),
            ],
            messages=[
                ModelRequest(parts=[UserPromptPart(content=prompt)]),
                ModelResponse(parts=[TextPart(content=output)]),
            ],
        )

    preview = build_session_preview(path=path, workspace_root=workspace_root)

    assert preview.session_id == path.stem
    assert preview.truncated is True
    assert [(entry.kind, entry.text) for entry in preview.entries] == [
        ("user", "prompt 3"),
        ("assistant", "answer 3"),
        ("user", "prompt 4"),
        ("assistant", "answer 4"),
        ("user", "prompt 5"),
        ("assistant", "answer 5"),
        ("user", "prompt 6"),
        ("assistant", "answer 6"),
        ("user", "prompt 7"),
        ("assistant", "answer 7"),
        ("user", "prompt 8"),
        ("assistant", "answer 8"),
        ("user", "prompt 9"),
        ("assistant", "answer 9"),
        ("user", "prompt 10"),
        ("assistant", "answer 10"),
        ("user", "prompt 11"),
        ("assistant", "answer 11"),
        ("user", "prompt 12"),
        ("assistant", "answer 12"),
    ]


def test_append_session_name_to_session_normalizes_and_persists_name(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    initialize_session(path=path, workspace_root=workspace_root)

    name = append_session_name_to_session(
        path=path,
        workspace_root=workspace_root,
        name="Auth Store Cleanup",
    )
    loaded = load_session(path=path, workspace_root=workspace_root)

    assert name == "auth-store-cleanup"
    assert loaded.name == "auth-store-cleanup"
    line_types = [json.loads(line)["type"] for line in path.read_text().splitlines()]
    assert line_types == ["session_header", "session_info"]
    metadata = read_session_metadata(path=path.with_suffix(".meta.json"))
    assert metadata.session_id == path.stem
    assert metadata.name == "auth-store-cleanup"
    assert metadata.consecutive_auto_compaction_failures == 0


def test_initialize_session_creates_metadata_sidecar(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    initialize_session(path=path, workspace_root=workspace_root)

    metadata = read_session_metadata(path=path.with_suffix(".meta.json"))
    assert metadata.session_id == path.stem
    assert metadata.name is None
    assert metadata.forked_from_session_id is None
    assert metadata.consecutive_auto_compaction_failures == 0


def test_load_session_uses_latest_session_name_entry(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    initialize_session(path=path, workspace_root=workspace_root)

    append_session_name_to_session(
        path=path,
        workspace_root=workspace_root,
        name="first pass",
    )
    append_session_name_to_session(
        path=path,
        workspace_root=workspace_root,
        name="second pass",
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.name == "second-pass"


def test_fork_session_copies_history_and_records_lineage(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = tmp_path / "source.jsonl"
    target_path = tmp_path / "fork.jsonl"

    initialize_session(path=source_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=source_path,
        workspace_root=workspace_root,
        name="source session",
    )
    append_run_to_session(
        path=source_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking="medium",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
    )

    fork_session(
        source_path=source_path,
        target_path=target_path,
        workspace_root=workspace_root,
        forked_from_session_id="a" * 32,
    )

    loaded = load_session(path=target_path, workspace_root=workspace_root)
    metadata = read_session_metadata(path=target_path.with_suffix(".meta.json"))
    raw_lines = target_path.read_text(encoding="utf-8").splitlines()
    line_types = [
        json.loads(line)["type"] for line in raw_lines
    ]

    assert loaded.name is None
    assert loaded.fork is not None
    assert loaded.fork.forked_from_session_id == "a" * 32
    assert loaded.fork.forked_from_run_id == "run-1"
    assert [run.prompt for run in loaded.runs] == ["first"]
    assert metadata.forked_from_session_id == "a" * 32
    assert line_types[:3] == ["session_header", "session_fork", "session_run"]


def test_fork_session_replaces_parent_fork_entry_with_direct_lineage(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = tmp_path / "source.jsonl"
    target_path = tmp_path / "fork.jsonl"

    initialize_session(path=source_path, workspace_root=workspace_root)
    append_run_to_session(
        path=source_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking="medium",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
    )
    intermediate_path = tmp_path / "intermediate.jsonl"
    fork_session(
        source_path=source_path,
        target_path=intermediate_path,
        workspace_root=workspace_root,
        forked_from_session_id="a" * 32,
    )

    fork_session(
        source_path=intermediate_path,
        target_path=target_path,
        workspace_root=workspace_root,
        forked_from_session_id="b" * 32,
    )

    loaded = load_session(path=target_path, workspace_root=workspace_root)
    raw_lines = target_path.read_text(encoding="utf-8").splitlines()
    line_types = [
        json.loads(line)["type"] for line in raw_lines
    ]

    assert loaded.fork is not None
    assert loaded.fork.forked_from_session_id == "b" * 32
    assert line_types.count("session_fork") == 1


def test_fork_session_drops_parent_turn_context_entries(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = tmp_path / "source.jsonl"
    target_path = tmp_path / "fork.jsonl"
    turn_context = SessionTurnContextEntry(
        run_id="run-1",
        model="openai-responses:gpt-5.3-codex",
        thinking="medium",
        workspace_root=str(workspace_root.resolve()),
        shell_family="posix",
        current_date="2026-04-04",
        runtime_context_text="Current workspace root: /workspace",
    )

    append_run_to_session(
        path=source_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking="medium",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        turn_context=turn_context,
    )

    fork_session(
        source_path=source_path,
        target_path=target_path,
        workspace_root=workspace_root,
        forked_from_session_id="a" * 32,
    )

    loaded = load_session(path=target_path, workspace_root=workspace_root)
    raw_lines = target_path.read_text(encoding="utf-8").splitlines()
    line_types = [json.loads(line)["type"] for line in raw_lines]

    assert loaded.turn_contexts == []
    assert loaded.latest_turn_context is None
    assert "session_turn_context" not in line_types


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
        thinking=None,
        events=first_events,
        messages=first_messages,
    )
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="second",
        thinking="medium",
        events=second_events,
        messages=second_messages,
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    line_types = [json.loads(line)["type"] for line in lines]

    assert line_types.count("session_header") == 1
    assert line_types.count("session_run") == 2
    assert line_types.count("session_messages") == 2
    assert line_types.count("session_event") == 4

    loaded = load_session(path=path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["first", "second"]
    assert loaded.message_history == first_messages + second_messages
    assert [run.thinking for run in loaded.runs] == [None, "medium"]
    assert loaded.thinking == "medium"


def test_append_run_to_session_writes_events_before_messages(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    line_types = [
        json.loads(line)["type"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]

    assert line_types == [
        "session_header",
        "session_run",
        "session_event",
        "session_event",
        "session_messages",
    ]


def test_append_run_to_session_does_not_persist_assistant_text_deltas(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="hello",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            AssistantTextDeltaEvent(run_id="run-1", delta="Hello"),
            AssistantTextDeltaEvent(run_id="run-1", delta=" world"),
            RunSucceededEvent(run_id="run-1", output_text="Hello world"),
        ],
        messages=[
            ModelRequest(parts=[UserPromptPart(content="hello")]),
            ModelResponse(parts=[TextPart(content="Hello world")]),
        ],
    )

    line_payloads = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    persisted_event_types = [
        payload["event"]["type"]
        for payload in line_payloads
        if payload["type"] == "session_event"
    ]

    assert persisted_event_types == ["run_started", "run_succeeded"]

    loaded = load_session(path=path, workspace_root=workspace_root)
    assert [event.type for event in loaded.runs[0].events] == [
        "run_started",
        "run_succeeded",
    ]


def test_append_and_load_session_preserves_tool_activity_metadata(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    run_events = [
        RunStartedEvent(run_id="run-1"),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-read",
            tool_name="read",
            args={"path": "note.txt"},
            args_valid=True,
            activity=ToolActivity(title="read note.txt"),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-read",
            tool_name="read",
            result="hello\nworld\n",
            activity=ToolActivity(
                title="read note.txt",
                summary="read completed",
                duration_ms=12,
                details={
                    "kind": "read",
                    "path": "note.txt",
                    "offset": None,
                    "limit": None,
                },
            ),
        ),
        RunSucceededEvent(run_id="run-1", output_text="done"),
    ]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=run_events,
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.runs[0].events == _persisted_events(run_events)


def test_append_and_load_session_preserves_edit_diff_activity_metadata(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    run_events = [
        RunStartedEvent(run_id="run-1"),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-edit",
            tool_name="edit",
            args={
                "path": "note.txt",
                "old_text": "world",
                "new_text": "agent",
            },
            args_valid=True,
            activity=ToolActivity(title="edit note.txt"),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-edit",
            tool_name="edit",
            result="Edited /tmp/workspace/note.txt",
            activity=ToolActivity(
                title="edit note.txt",
                summary="edit applied",
                duration_ms=12,
                details={
                    "kind": "edit",
                    "path": "note.txt",
                    "diff": (
                        "--- /tmp/workspace/note.txt\n"
                        "+++ /tmp/workspace/note.txt\n"
                        "@@ -1 +1 @@\n"
                        "-world\n"
                        "+agent\n"
                    ),
                    "added_lines": 1,
                    "removed_lines": 1,
                },
            ),
        ),
        RunSucceededEvent(run_id="run-1", output_text="done"),
    ]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=run_events,
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.runs[0].events == _persisted_events(run_events)


def test_append_and_load_session_preserves_tool_call_updates(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    run_events = [
        RunStartedEvent(run_id="run-1"),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="shell",
            args={"command": "sleep 1"},
            args_valid=True,
            activity=ToolActivity(title="shell sleep 1"),
        ),
        ToolCallUpdatedEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="shell",
            partial_result={"output": "still running"},
            activity=ToolActivity(
                title="shell sleep 1",
                summary="command still running",
                duration_ms=250,
            ),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="shell",
            result={"exit_code": 0, "output": "done"},
            activity=ToolActivity(
                title="shell sleep 1",
                summary="command exited 0",
                duration_ms=500,
                details={
                    "kind": "shell",
                    "command_preview": "sleep 1",
                    "shell_family": "posix",
                    "timeout": None,
                    "exit_code": 0,
                },
            ),
        ),
        RunSucceededEvent(run_id="run-1", output_text="done"),
    ]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=run_events,
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.runs[0].events == _persisted_events(run_events)


def test_append_and_load_session_preserves_interleaved_parallel_tool_calls(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    run_events = [
        RunStartedEvent(run_id="run-1"),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-read-a",
            tool_name="read",
            args={"path": "a.txt"},
            args_valid=True,
            activity=ToolActivity(title="read a.txt"),
        ),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-read-b",
            tool_name="read",
            args={"path": "b.txt"},
            args_valid=True,
            activity=ToolActivity(title="read b.txt"),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-read-b",
            tool_name="read",
            result="beta",
            activity=ToolActivity(
                title="read b.txt",
                summary="read completed",
                duration_ms=9,
                details={
                    "kind": "read",
                    "path": "b.txt",
                    "offset": None,
                    "limit": None,
                },
            ),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-read-a",
            tool_name="read",
            result="alpha",
            activity=ToolActivity(
                title="read a.txt",
                summary="read completed",
                duration_ms=12,
                details={
                    "kind": "read",
                    "path": "a.txt",
                    "offset": None,
                    "limit": None,
                },
            ),
        ),
        RunSucceededEvent(run_id="run-1", output_text="done"),
    ]

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=run_events,
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert loaded.runs[0].events == _persisted_events(run_events)


def test_load_session_fails_without_header(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
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
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_tool_update_has_no_started_call(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lines = [
        {
            "type": "session_header",
            "version": SESSION_FORMAT_VERSION,
            "workspace_root": str(workspace_root.resolve()),
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
                "type": "tool_call_updated",
                "run_id": "run-1",
                "tool_call_id": "call-bash",
                "tool_name": "shell",
                "partial_result": {"output": "still running"},
                "activity": None,
            },
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
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="Tool update must follow tool_call_started",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_trailing_run_is_incomplete(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lines = [
        {
            "type": "session_header",
            "version": SESSION_FORMAT_VERSION,
            "workspace_root": str(workspace_root.resolve()),
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "go"},
        {
            "type": "session_event",
            "run_id": "run-1",
            "event": {"type": "run_started", "run_id": "run-1"},
        },
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="Session ended with incomplete run",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_file_is_empty(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path.write_text("", encoding="utf-8")

    with pytest.raises(SessionFormatError, match="Session file is empty"):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_on_duplicate_run_id(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lines = [
        {
            "type": "session_header",
            "version": SESSION_FORMAT_VERSION,
            "workspace_root": str(workspace_root.resolve()),
        },
        {"type": "session_run", "run_id": "run-1", "prompt": "first"},
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
        {"type": "session_run", "run_id": "run-1", "prompt": "second"},
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Duplicate session run_id: run-1"):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_on_unsupported_header_version(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "version": 999,
                "workspace_root": str(workspace_root.resolve()),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="Unsupported session format version on line 1: 999",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_run_event_order_is_invalid(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lines = [
        {
            "type": "session_header",
            "version": SESSION_FORMAT_VERSION,
            "workspace_root": str(workspace_root.resolve()),
        },
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
    ]
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Run must start with run_started"):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_header_has_no_workspace_root(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path.write_text(
        json.dumps({"type": "session_header", "version": SESSION_FORMAT_VERSION})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionFormatError, match="Invalid session entry on line 1"):
        load_session(path=path, workspace_root=workspace_root)


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
        thinking=None,
        events=run_events,
        messages=run_messages,
    )

    with pytest.raises(SessionFormatError, match="Session workspace_root mismatch"):
        load_session(path=path, workspace_root=other_workspace)


def test_load_session_allows_cross_host_shell_family_mismatch(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        shell_family="posix",
        prompt="go",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    loaded = load_session(
        path=path,
        workspace_root=workspace_root,
        shell_family="powershell",
    )

    assert loaded.header.shell_family == "posix"
    assert loaded.runs[0].run_id == "run-1"


def test_load_session_fails_when_session_messages_are_missing(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lines = [
        {
            "type": "session_header",
            "version": SESSION_FORMAT_VERSION,
            "workspace_root": str(workspace_root.resolve()),
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
        match="Session ended with incomplete run",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_append_run_to_session_rejects_messages_with_unresolved_tool_calls(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(
        SessionFormatError,
        match="Session messages cannot contain unresolved tool calls",
    ):
        append_run_to_session(
            path=path,
            workspace_root=workspace_root,
            prompt="go",
            thinking=None,
            events=[
                RunStartedEvent(run_id="run-1"),
                ToolCallStartedEvent(
                    run_id="run-1",
                    tool_call_id="call-read",
                    tool_name="read",
                    args={"path": "README.md"},
                    args_valid=True,
                ),
                ToolCallFailedEvent(
                    run_id="run-1",
                    tool_call_id="call-read",
                    tool_name="read",
                    error_type="CancelledError",
                    message="run cancelled",
                ),
                RunFailedEvent(
                    run_id="run-1",
                    error_type="CancelledError",
                    message="run cancelled",
                ),
            ],
            messages=[
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
            ],
        )


def test_load_session_requires_workspace_root(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )

    with pytest.raises(TypeError):
        load_session(path=path)


def test_append_run_to_session_rejects_persisted_internal_instructions(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(
        SessionFormatError,
        match="Session messages must not persist internal instructions",
    ):
        append_run_to_session(
            path=path,
            workspace_root=workspace_root,
            prompt="go",
            events=[
                RunStartedEvent(run_id="run-1"),
                RunSucceededEvent(run_id="run-1", output_text="done"),
            ],
            messages=[
                ModelRequest(
                    parts=[UserPromptPart(content="go")],
                    instructions="internal only",
                )
            ],
        )


def test_append_compaction_to_session_appends_replacement_messages(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="create note",
        thinking="high",
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="create note")])],
    )

    replacement_messages = [
        ModelRequest(parts=[UserPromptPart(content="create note")]),
        build_compaction_summary_message("ship note creation"),
    ]
    compaction = append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=replacement_messages,
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert compaction.compacted_through_run_id == "run-1"
    assert compaction.replacement_messages == replacement_messages
    assert loaded.compactions == [compaction]


def test_append_compaction_to_session_accepts_explicit_compaction_boundary(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for run_id, prompt in [("run-1", "first"), ("run-2", "second")]:
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

    compaction = append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        compacted_through_run_id="run-1",
        replacement_messages=[build_compaction_summary_message("continue")],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert compaction.compacted_through_run_id == "run-1"
    assert loaded.compactions == [compaction]


def test_append_compaction_to_session_preserves_replacement_messages(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for run_id, prompt in [("run-1", "first"), ("run-2", "second")]:
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

    replacement_messages = [
        ModelRequest(parts=[UserPromptPart(content="second")]),
        build_compaction_summary_message("continue"),
    ]
    compaction = append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=replacement_messages,
    )

    assert compaction.replacement_messages == replacement_messages


def test_append_compaction_to_session_accepts_custom_replacement_messages(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for run_id, prompt in [("run-1", "first"), ("run-2", "second")]:
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

    custom_replacement_messages = [
        ModelResponse(parts=[TextPart(content="retained tail")], model_name="test"),
        build_compaction_summary_message("continue"),
    ]
    compaction = append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        compacted_through_run_id="run-2",
        replacement_messages=custom_replacement_messages,
    )

    assert compaction.compacted_through_run_id == "run-2"
    assert compaction.replacement_messages == custom_replacement_messages


def test_load_session_accepts_replacement_history_compaction_entry(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for run_id, prompt in [("run-1", "first"), ("run-2", "second")]:
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

    path.write_text(
        path.read_text(encoding="utf-8")
        + json.dumps(
            _compaction_entry_payload(
                compacted_through_run_id="run-2",
                replacement_messages=[
                    ModelResponse(
                        parts=[TextPart(content="retained tail")],
                        model_name="test",
                    ),
                    build_compaction_summary_message("continue"),
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_session(path=path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.compacted_through_run_id == "run-2"


def test_append_compaction_to_session_rejects_empty_session(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    initialize_session(path=path, workspace_root=workspace_root)

    with pytest.raises(
        SessionFormatError,
        match="Cannot compact a session with no completed runs",
    ):
        append_compaction_to_session(
            path=path,
            workspace_root=workspace_root,
            replacement_messages=[build_compaction_summary_message("continue")],
        )


def test_load_session_tracks_compaction_entries_without_changing_message_history(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
    )
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="second",
        thinking="high",
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
    )

    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(
            json.dumps(
                _compaction_entry_payload(
                    compacted_through_run_id="run-2",
                    replacement_messages=[
                        build_compaction_summary_message("Continue the task"),
                    ],
                )
            )
            + "\n"
        )

    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="third",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-3"),
            RunSucceededEvent(run_id="run-3", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="third")])],
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert [run.run_id for run in loaded.runs] == ["run-1", "run-2", "run-3"]
    assert len(loaded.compactions) == 1
    assert loaded.compactions[0].compaction_id == "compact-1"
    assert loaded.compactions[0].compacted_through_run_id == "run-2"
    assert loaded.latest_compaction == loaded.compactions[0]
    assert [message.parts[0].content for message in loaded.message_history] == [
        "first",
        "second",
        "third",
    ]


def test_load_session_fails_when_compaction_precedes_any_run(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_header",
                        "version": SESSION_FORMAT_VERSION,
                        "workspace_root": str(workspace_root.resolve()),
                    }
                ),
                json.dumps(
                    _compaction_entry_payload(
                        compacted_through_run_id="run-1",
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="Session compaction entry must follow at least one complete run",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_compaction_references_unknown_run_id(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="go",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="go")])],
    )
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(
            json.dumps(
                _compaction_entry_payload(
                    compacted_through_run_id="run-999",
                )
            )
            + "\n"
        )

    with pytest.raises(
        SessionFormatError,
        match="Session compaction entry must reference an existing run_id",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_compaction_replacement_messages_are_empty(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
    )
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
    )
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(
            json.dumps(
                _compaction_entry_payload(
                    compacted_through_run_id="run-2",
                    replacement_messages=[],
                )
            )
            + "\n"
        )

    with pytest.raises(
        SessionFormatError,
        match="Session compaction replacement_messages must be non-empty",
    ):
        load_session(path=path, workspace_root=workspace_root)


def test_load_session_fails_when_compaction_summary_message_is_missing(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
    )
    append_run_to_session(
        path=path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
    )
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(
            json.dumps(
                _compaction_entry_payload(
                    compacted_through_run_id="run-1",
                    replacement_messages=[
                        ModelRequest(parts=[UserPromptPart(content="first")]),
                    ],
                )
            )
            + "\n"
        )

    with pytest.raises(
        SessionFormatError,
        match=(
            "Session compaction replacement_messages must end with a "
            "compaction summary message"
        ),
    ):
        load_session(path=path, workspace_root=workspace_root)
