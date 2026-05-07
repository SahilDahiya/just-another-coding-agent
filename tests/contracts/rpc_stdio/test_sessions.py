from pydantic_ai.models.function import FunctionModel

import just_another_coding_agent.rpc.stdio as rpc_stdio
from just_another_coding_agent.rpc.session_store import (
    create_session,
    session_path_for_id,
)
from just_another_coding_agent.session import load_session
from just_another_coding_agent.session.jsonl import read_session_metadata
from tests.contracts.rpc_stdio_test_support import (
    create_session_id,
    looping_edit_stream,
    resume_aware_write_stream,
    resume_or_compaction_stream,
    rpc_messages,
    text_only_stream,
)


async def test_handle_rpc_json_line_creates_session_and_resumes_runs(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    model = FunctionModel(stream_function=resume_aware_write_stream)

    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    first_messages = await rpc_messages(
        request_payload={
            "id": "req-1",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "create note",
                "thinking": "high",
            },
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    second_messages = await rpc_messages(
        request_payload={
            "id": "req-2",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "what did you do?",
                "thinking": "high",
            },
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert [message["type"] for message in first_messages] == (["rpc_event"] * 6) + [
        "rpc_response"
    ]
    assert [message["event"]["type"] for message in first_messages[:-1]] == [
        "session_turn_context_status",
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert first_messages[0]["event"]["status"] == "missing"
    assert first_messages[0]["event"]["reason"] == "missing"
    assert first_messages[-2]["event"]["output_text"] == "created"
    assert first_messages[-1] == {
        "type": "rpc_response",
        "id": "req-1",
        "response": {"session_id": session_id},
    }

    assert [message["type"] for message in second_messages] == (["rpc_event"] * 4) + [
        "rpc_response"
    ]
    assert [message["event"]["type"] for message in second_messages[:-1]] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert second_messages[0]["event"]["status"] == "reused"
    assert second_messages[0]["event"]["reason"] == "matched"
    assert second_messages[-2]["event"]["output_text"] == "I created note.txt"
    assert second_messages[-1] == {
        "type": "rpc_response",
        "id": "req-2",
        "response": {"session_id": session_id},
    }

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]
    assert [run.thinking for run in loaded.runs] == ["high", "high"]


async def test_handle_rpc_json_line_returns_session_preview(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    model = FunctionModel(stream_function=resume_aware_write_stream)

    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    await rpc_messages(
        request_payload={
            "id": "req-1",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "create note",
            },
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await rpc_messages(
        request_payload={
            "id": "req-2",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "what did you do?",
            },
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-preview",
            "command": "session.preview",
            "payload": {"session_id": session_id},
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert len(messages) == 1
    response = messages[0]
    assert response["type"] == "rpc_response"
    assert response["id"] == "req-preview"
    assert response["response"]["session_id"] == session_id
    assert response["response"]["truncated"] is False
    entries = response["response"]["entries"]
    assert entries[0] == {"kind": "user", "text": "create note"}
    assert entries[1]["kind"] == "activity"
    assert entries[1]["text"].startswith("Edited files - 1 file")
    assert entries[2] == {"kind": "assistant", "text": "created"}
    assert entries[3] == {"kind": "user", "text": "what did you do?"}
    assert entries[4] == {"kind": "assistant", "text": "I created note.txt"}


async def test_handle_rpc_json_line_names_session_with_backend_normalization(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-name",
            "command": "session.name",
            "payload": {
                "session_id": session_id,
                "name": "Auth Store Cleanup",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-name",
            "response": {
                "session_id": session_id,
                "name": "auth-store-cleanup",
            },
        }
    ]

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.name == "auth-store-cleanup"


async def test_handle_rpc_json_line_returns_provider_not_ready_for_run_start(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = create_session(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "auth.json",
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-run-not-ready",
            "command": "run.start",
            "payload": {
                "session_id": session_path,
                "prompt": "hello",
            },
        },
        model="anthropic:claude-sonnet-4-5",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-run-not-ready",
            "error_type": "ProviderNotReady",
            "message": "anthropic is not ready: missing_secret",
        }
    ]


async def test_handle_rpc_json_line_uses_default_run_mode_toolset(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: dict[str, object] = {}

    async def fake_stream_session_run_events(**kwargs):
        captured["tool_names"] = kwargs["tool_names"]
        captured["run_mode"] = kwargs["run_mode"]
        yield {"type": "run_started", "run_id": "run-1"}
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        rpc_stdio,
        "stream_session_run_events",
        fake_stream_session_run_events,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-run-default",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "hello",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {
        "tool_names": (
            "read",
            "write",
            "edit",
            "shell",
            "grep",
            "ls",
            "find",
            "subagent",
        ),
        "run_mode": "coding",
    }
    assert messages[-1] == {
        "type": "rpc_response",
        "id": "req-run-default",
        "response": {"session_id": session_id},
    }
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.current_mode == "coding"


async def test_handle_rpc_json_line_uses_onboarding_run_mode_toolset(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: dict[str, object] = {}

    async def fake_stream_session_run_events(**kwargs):
        captured["tool_names"] = kwargs["tool_names"]
        captured["run_mode"] = kwargs["run_mode"]
        yield {"type": "run_started", "run_id": "run-1"}
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        rpc_stdio,
        "stream_session_run_events",
        fake_stream_session_run_events,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-run-onboarding",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "hello",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {
        "tool_names": (
            "read",
            "write",
            "edit",
            "shell",
            "grep",
            "ls",
            "find",
            "subagent",
            "ask_mcq_question",
            "publish_teaching_packet",
        ),
        "run_mode": "onboarding",
    }
    assert messages[-1] == {
        "type": "rpc_response",
        "id": "req-run-onboarding",
        "response": {"session_id": session_id},
    }


async def test_handle_rpc_json_line_inherits_persisted_onboarding_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: list[dict[str, object]] = []

    async def fake_stream_session_run_events(**kwargs):
        captured.append(
            {
                "tool_names": kwargs["tool_names"],
                "run_mode": kwargs["run_mode"],
            }
        )
        yield {"type": "run_started", "run_id": f"run-{len(captured)}"}
        yield {
            "type": "run_succeeded",
            "run_id": f"run-{len(captured)}",
            "output_text": "done",
        }

    monkeypatch.setattr(
        rpc_stdio,
        "stream_session_run_events",
        fake_stream_session_run_events,
    )

    await rpc_messages(
        request_payload={
            "id": "req-run-onboarding",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "hello",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    await rpc_messages(
        request_payload={
            "id": "req-run-followup",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "one more",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == [
        {
            "tool_names": (
                "read",
                "write",
                "edit",
                "shell",
                "grep",
                "ls",
                "find",
                "subagent",
                "ask_mcq_question",
                "publish_teaching_packet",
            ),
            "run_mode": "onboarding",
        },
        {
            "tool_names": (
                "read",
                "write",
                "edit",
                "shell",
                "grep",
                "ls",
                "find",
                "subagent",
                "ask_mcq_question",
                "publish_teaching_packet",
            ),
            "run_mode": "onboarding",
        },
    ]
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.current_mode == "onboarding"


async def test_handle_rpc_json_line_session_mode_set_restores_coding_inheritance(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: list[str] = []

    async def fake_stream_session_run_events(**kwargs):
        captured.append(kwargs["run_mode"])
        yield {"type": "run_started", "run_id": f"run-{len(captured)}"}
        yield {
            "type": "run_succeeded",
            "run_id": f"run-{len(captured)}",
            "output_text": "done",
        }

    monkeypatch.setattr(
        rpc_stdio,
        "stream_session_run_events",
        fake_stream_session_run_events,
    )

    await rpc_messages(
        request_payload={
            "id": "req-run-onboarding",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "hello",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    mode_messages = await rpc_messages(
        request_payload={
            "id": "req-mode",
            "command": "session.mode_set",
            "payload": {
                "session_id": session_id,
                "mode": "coding",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert mode_messages == [
        {
            "type": "rpc_response",
            "id": "req-mode",
            "response": {
                "session_id": session_id,
                "mode": "coding",
            },
        }
    ]

    await rpc_messages(
        request_payload={
            "id": "req-run-followup",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "back to coding",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == ["onboarding", "coding"]
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    metadata = read_session_metadata(path=session_path.with_suffix(".meta.json"))
    assert metadata.current_mode == "coding"


async def test_handle_rpc_json_line_compacts_session_and_returns_metadata(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    model = FunctionModel(stream_function=resume_or_compaction_stream)

    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await rpc_messages(
        request_payload={
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "create note",
                "thinking": "high",
            },
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    created_run_id = (
        load_session(
            path=session_path,
            workspace_root=workspace_root,
        )
        .runs[0]
        .run_id
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-compact",
            "command": "session.compact",
            "payload": {"session_id": session_id},
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-compact",
            "response": {
                "compaction_id": messages[0]["response"]["compaction_id"],
                "compacted_through_run_id": created_run_id,
            },
        }
    ]
    assert len(messages[0]["response"]["compaction_id"]) == 32

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert (
        loaded.latest_compaction.compaction_id
        == messages[0]["response"]["compaction_id"]
    )
    assert loaded.latest_compaction.compacted_through_run_id == created_run_id


async def test_handle_rpc_json_line_returns_invalid_session_for_empty_compaction(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-compact-empty",
            "command": "session.compact",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-compact-empty",
            "error_type": "InvalidSession",
            "message": "Cannot compact a session with no completed runs",
        }
    ]


async def test_handle_rpc_json_line_forwards_explicit_thinking_to_session_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: dict[str, object] = {}

    async def fake_stream_session_run_events(
        *,
        model,
        workspace_root,
        session_path,
        prompt,
        tool_names,
        thinking=None,
        **_kwargs,
    ):
        captured["thinking"] = thinking
        captured["prompt"] = prompt
        yield {"type": "run_started", "run_id": "run-1"}
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-thinking",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "go",
                "thinking": "high",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {"thinking": "high", "prompt": "go"}
    assert [message["type"] for message in messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
    assert [message["event"]["type"] for message in messages[:-1]] == [
        "run_started",
        "run_succeeded",
    ]


async def test_handle_rpc_json_line_keeps_run_failure_in_event_stream_and_session(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-2",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "go"},
        },
        model=FunctionModel(stream_function=looping_edit_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert [message["type"] for message in messages] == (["rpc_event"] * 10) + [
        "rpc_response"
    ]
    assert [message["event"]["type"] for message in messages[:-1]] == [
        "session_turn_context_status",
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
    assert messages[0]["event"]["status"] == "missing"
    assert messages[0]["event"]["reason"] == "missing"
    assert messages[3]["event"]["result"] == {
        "ok": False,
        "error_type": "ToolMatchError",
        "message": (
            "old_text must match exactly once in "
            f"{workspace_root / 'note.txt'}; found 0 occurrences"
        ),
    }
    assert messages[5]["event"]["result"] == messages[3]["event"]["result"]
    assert messages[7]["event"]["result"] == messages[3]["event"]["result"]
    assert messages[-3]["event"]["delta"] == "done"
    assert messages[-2]["event"]["output_text"] == "done"
    assert messages[-1] == {
        "type": "rpc_response",
        "id": "req-2",
        "response": {"session_id": session_id},
    }

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs[0].prompt == "go"
    assert [event.type for event in loaded.runs[0].events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_started",
        "tool_call_succeeded",
        "run_succeeded",
    ]


async def test_handle_rpc_json_line_returns_unknown_session_error(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await rpc_messages(
        request_payload={
            "id": "req-unknown",
            "command": "run.start",
            "payload": {"session_id": "0" * 32, "prompt": "go"},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-unknown",
            "error_type": "UnknownSession",
            "message": f"Unknown session_id: {'0' * 32}",
        }
    ]


async def test_handle_rpc_json_line_returns_invalid_session_error_on_workspace_mismatch(
    tmp_path,
) -> None:
    first_workspace_root = tmp_path / "workspace-a"
    first_workspace_root.mkdir()
    second_workspace_root = tmp_path / "workspace-b"
    second_workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    session_id = await create_session_id(
        workspace_root=first_workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-mismatch",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "go"},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=second_workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-mismatch",
            "error_type": "UnknownSession",
            "message": f"Unknown session_id: {session_id}",
        }
    ]


async def test_handle_rpc_json_line_lists_workspace_project_docs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    (workspace_root / "AGENTS.md").write_text("Read docs first.\n", encoding="utf-8")
    (workspace_root / "CLAUDE.md").write_text(
        "Be repo-grounded.\n",
        encoding="utf-8",
    )

    accept_messages = await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert accept_messages == [
        {
            "type": "rpc_response",
            "id": "req-trust-accept",
            "response": {
                "trusted": True,
                "trust_target": str(workspace_root),
            },
        }
    ]

    messages = await rpc_messages(
        request_payload={
            "id": "req-project-docs",
            "command": "workspace.project_docs",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-project-docs",
            "response": {
                "documents": [
                    {
                        "path": str(workspace_root / "AGENTS.md"),
                        "filename": "AGENTS.md",
                        "truncated": False,
                    },
                    {
                        "path": str(workspace_root / "CLAUDE.md"),
                        "filename": "CLAUDE.md",
                        "truncated": False,
                    },
                ]
            },
        }
    ]


async def test_workspace_trust_status_accept_and_session_gate(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace_root = tmp_path / "repo" / "nested"
    workspace_root.mkdir(parents=True)
    repo_root = workspace_root.parent
    (repo_root / ".git").mkdir()
    (repo_root / "AGENTS.md").write_text("Read docs first.\n", encoding="utf-8")
    sessions_root = tmp_path / "sessions"

    status_messages = await rpc_messages(
        request_payload={
            "id": "req-trust-status",
            "command": "workspace.trust_status",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert status_messages == [
        {
            "type": "rpc_response",
            "id": "req-trust-status",
            "response": {
                "trusted": False,
                "trust_target": str(repo_root),
            },
        }
    ]

    create_messages = await rpc_messages(
        request_payload={
            "id": "req-create-untrusted",
            "command": "session.create",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert create_messages == [
        {
            "type": "rpc_error",
            "id": "req-create-untrusted",
            "error_type": "WorkspaceUntrusted",
            "message": (
                "Workspace is not trusted yet. Accept trust for "
                f"{repo_root} before loading project instructions or "
                "starting a session."
            ),
        }
    ]

    docs_messages = await rpc_messages(
        request_payload={
            "id": "req-docs-untrusted",
            "command": "workspace.project_docs",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert docs_messages == [
        {
            "type": "rpc_error",
            "id": "req-docs-untrusted",
            "error_type": "WorkspaceUntrusted",
            "message": (
                "Workspace is not trusted yet. Accept trust for "
                f"{repo_root} before loading project instructions or "
                "starting a session."
            ),
        }
    ]

    accept_messages = await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert accept_messages == [
        {
            "type": "rpc_response",
            "id": "req-trust-accept",
            "response": {
                "trusted": True,
                "trust_target": str(repo_root),
            },
        }
    ]

    trusted_create_messages = await rpc_messages(
        request_payload={
            "id": "req-create-trusted",
            "command": "session.create",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert trusted_create_messages[0]["type"] == "rpc_response"
    assert trusted_create_messages[0]["id"] == "req-create-trusted"
    assert trusted_create_messages[0]["response"]["project_docs"] == [
        {
            "path": str(repo_root / "AGENTS.md"),
            "filename": "AGENTS.md",
            "truncated": False,
        }
    ]

    trusted_docs_messages = await rpc_messages(
        request_payload={
            "id": "req-docs-trusted",
            "command": "workspace.project_docs",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert trusted_docs_messages == [
        {
            "type": "rpc_response",
            "id": "req-docs-trusted",
            "response": {
                "documents": [
                    {
                        "path": str(repo_root / "AGENTS.md"),
                        "filename": "AGENTS.md",
                        "truncated": False,
                    }
                ]
            },
        }
    ]


async def test_handle_rpc_json_line_session_preview_includes_project_docs_note(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    (workspace_root / "AGENTS.md").write_text("Read docs first.\n", encoding="utf-8")
    session_id = create_session(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-session-preview",
            "command": "session.preview",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-session-preview",
            "response": {
                "session_id": session_id,
                "entries": [
                    {
                        "kind": "instructions",
                        "text": "loaded project instructions: AGENTS.md",
                    }
                ],
                "truncated": False,
            },
        }
    ]
