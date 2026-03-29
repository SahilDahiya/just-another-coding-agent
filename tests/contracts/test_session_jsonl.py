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

from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolActivity,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.contracts.session import (
    SESSION_FORMAT_VERSION,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.session.jsonl import (
    SessionFormatError,
    append_compaction_to_session,
    append_run_to_session,
    initialize_session,
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
    assert loaded.runs[0].events == events
    assert loaded.message_history == messages
    assert loaded.thinking == "high"


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
    assert line_types.count("session_event") == 5

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

    assert loaded.runs[0].events == run_events


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

    assert loaded.runs[0].events == run_events


def test_append_and_load_session_preserves_tool_call_updates(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    run_events = [
        RunStartedEvent(run_id="run-1"),
        ToolCallStartedEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="bash",
            args={"command": "sleep 1"},
            args_valid=True,
            activity=ToolActivity(title="bash sleep 1"),
        ),
        ToolCallUpdatedEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="bash",
            partial_result={"output": "still running"},
            activity=ToolActivity(
                title="bash sleep 1",
                summary="command still running",
                duration_ms=250,
            ),
        ),
        ToolCallSucceededEvent(
            run_id="run-1",
            tool_call_id="call-bash",
            tool_name="bash",
            result={"exit_code": 0, "output": "done"},
            activity=ToolActivity(
                title="bash sleep 1",
                summary="command exited 0",
                duration_ms=500,
                details={
                    "kind": "bash",
                    "command_preview": "sleep 1",
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

    assert loaded.runs[0].events == run_events


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

    assert loaded.runs[0].events == run_events


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
                "tool_name": "bash",
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


def test_append_compaction_to_session_appends_provided_summary(tmp_path) -> None:
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

    summary = SessionCompactionSummary(
        current_objective="ship note creation",
        established_facts=["note.txt was created"],
        user_preferences=["be concise"],
        important_paths=["note.txt"],
        open_questions=["should we add logging?"],
        unresolved_work=["verify the final behavior"],
    )
    compaction = append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        summary=summary,
    )

    loaded = load_session(path=path, workspace_root=workspace_root)

    assert compaction.summarized_through_run_id == "run-1"
    assert compaction.summary == summary
    assert loaded.compactions == [compaction]


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
            summary=SessionCompactionSummary(),
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
                {
                    "type": "session_compaction",
                    "compaction_id": "compact-1",
                    "summarized_through_run_id": "run-2",
                    "summary": {
                        "current_objective": "Continue the task",
                        "established_facts": ["first and second completed"],
                        "user_preferences": ["be concise"],
                        "important_paths": ["src/app.py"],
                        "open_questions": [],
                        "unresolved_work": ["ship the fix"],
                    },
                }
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
    assert loaded.compactions[0].summarized_through_run_id == "run-2"
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
                    {
                        "type": "session_compaction",
                        "compaction_id": "compact-1",
                        "summarized_through_run_id": "run-1",
                        "summary": {
                            "current_objective": None,
                            "established_facts": [],
                            "user_preferences": [],
                            "important_paths": [],
                            "open_questions": [],
                            "unresolved_work": [],
                        },
                    }
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
                {
                    "type": "session_compaction",
                    "compaction_id": "compact-1",
                    "summarized_through_run_id": "run-999",
                    "summary": {
                        "current_objective": "go",
                        "established_facts": [],
                        "user_preferences": [],
                        "important_paths": [],
                        "open_questions": [],
                        "unresolved_work": [],
                    },
                }
            )
            + "\n"
        )

    with pytest.raises(
        SessionFormatError,
        match="Session compaction entry must reference an existing run_id",
    ):
        load_session(path=path, workspace_root=workspace_root)
