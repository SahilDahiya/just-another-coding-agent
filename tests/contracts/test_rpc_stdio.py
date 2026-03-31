import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.auth import ProviderAuthStatus
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.rpc.stdio import handle_rpc_json_line
from just_another_coding_agent.session import load_session


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


async def resume_aware_write_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
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


async def looping_edit_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) >= 7:
        yield "done"
        return

    yield {
        0: DeltaToolCall(
            name="edit",
            json_args=(
                '{"path": "note.txt", "old_text": "missing", "new_text": "agent"}'
            ),
            tool_call_id=f"call-edit-{len(messages)}",
        )
    }


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def compaction_summary_function(
    messages: list[ModelMessage],
    _agent_info: object,
) -> ModelResponse:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Runs since the latest compaction boundary:" in prompt
    assert "Prompt: create note" in prompt
    assert "create note" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps(
                    {
                        "current_objective": "finish note handling",
                        "established_facts": ["note.txt was created"],
                        "user_preferences": ["be concise"],
                        "important_paths": ["note.txt"],
                        "read_paths": [],
                        "modified_paths": ["note.txt"],
                        "open_questions": ["Should we add logging?"],
                        "unresolved_work": ["Run the final verifier."],
                    }
                )
            )
        ]
    )


async def exploding_session_stream(*_args, **_kwargs):
    raise RuntimeError("internal boom")
    yield  # pragma: no cover


async def _rpc_messages(
    *,
    request_payload: object,
    model,
    workspace_root,
    sessions_root,
) -> list[dict[str, object]]:
    request_line = json.dumps(request_payload)
    return [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=model,
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        )
    ]


async def _create_session_id(*, workspace_root, sessions_root) -> str:
    messages = await _rpc_messages(
        request_payload={
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages[0]["type"] == "rpc_response"
    assert messages[0]["id"] == "req-create"
    session_id = str(messages[0]["response"]["session_id"])
    assert len(session_id) == 32
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        session_id=session_id,
    )
    assert session_path.exists()
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs == []
    return session_id


async def test_handle_rpc_json_line_creates_session_and_resumes_runs(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    model = FunctionModel(stream_function=resume_aware_write_stream)

    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    first_messages = await _rpc_messages(
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
    second_messages = await _rpc_messages(
        request_payload={
            "id": "req-2",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "what did you do?"},
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert [message["type"] for message in first_messages] == ["rpc_event"] * 5
    assert [message["event"]["type"] for message in first_messages] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert first_messages[-1]["event"]["output_text"] == "created"

    assert [message["type"] for message in second_messages] == ["rpc_event"] * 3
    assert [message["event"]["type"] for message in second_messages] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert second_messages[-1]["event"]["output_text"] == "I created note.txt"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        session_id=session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]
    assert [run.thinking for run in loaded.runs] == ["high", "high"]


async def test_handle_rpc_json_line_returns_backend_owned_model_catalog(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await _rpc_messages(
        request_payload={
            "id": "req-catalog",
            "command": "model.catalog",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-catalog",
            "response": {
                "providers": [
                    {
                        "provider": "ollama",
                        "default_model_id": "ollama:kimi-k2:1t-cloud",
                        "models": [
                            {
                                "model_id": "ollama:kimi-k2:1t-cloud",
                                "description": "Current default Kimi K2",
                            },
                            {
                                "model_id": "ollama:glm-5:cloud",
                                "description": "GLM-5 cloud path",
                            },
                            {
                                "model_id": "ollama:qwen3.5:397b-cloud",
                                "description": "Qwen 3.5 397B cloud",
                            },
                            {
                                "model_id": "ollama:qwen3-coder-next",
                                "description": "Qwen3 Coder Next",
                            },
                        ],
                    },
                    {
                        "provider": "github",
                        "default_model_id": "github:openai/gpt-5",
                        "models": [
                            {
                                "model_id": "github:openai/gpt-5",
                                "description": "GitHub Models GPT-5",
                            },
                            {
                                "model_id": "github:openai/gpt-5-mini",
                                "description": "GitHub Models GPT-5 mini",
                            },
                            {
                                "model_id": "github:openai/gpt-4.1",
                                "description": "GitHub Models GPT-4.1",
                            },
                        ],
                    },
                    {
                        "provider": "openai",
                        "default_model_id": "openai:gpt-5.4",
                        "models": [
                            {
                                "model_id": "openai:gpt-5.4",
                                "description": "Default GPT-5.4 path",
                            },
                            {
                                "model_id": "openai:gpt-5.4-mini",
                                "description": "Faster GPT-5.4 mini",
                            },
                            {
                                "model_id": "openai:gpt-5.3-codex",
                                "description": "Codex-optimized GPT-5.3",
                            },
                        ],
                    },
                    {
                        "provider": "anthropic",
                        "default_model_id": "anthropic:claude-sonnet-4-5",
                        "models": [
                            {
                                "model_id": "anthropic:claude-sonnet-4-5",
                                "description": "Balanced Claude Sonnet",
                            },
                            {
                                "model_id": "anthropic:claude-opus-4-1",
                                "description": "Stronger Claude Opus",
                            },
                        ],
                    },
                ]
            },
        }
    ]


async def test_handle_rpc_json_line_returns_auth_status(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.list_provider_auth_statuses",
        lambda: [
            ProviderAuthStatus(provider="ollama", configured=False, source="none"),
            ProviderAuthStatus(provider="github", configured=True, source="keychain"),
            ProviderAuthStatus(provider="openai", configured=True, source="env"),
            ProviderAuthStatus(provider="anthropic", configured=False, source="none"),
        ],
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-status",
            "command": "auth.status",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-auth-status",
            "response": {
                "providers": [
                    {
                        "provider": "ollama",
                        "configured": False,
                        "source": "none",
                    },
                    {
                        "provider": "github",
                        "configured": True,
                        "source": "keychain",
                    },
                    {
                        "provider": "openai",
                        "configured": True,
                        "source": "env",
                    },
                    {
                        "provider": "anthropic",
                        "configured": False,
                        "source": "none",
                    },
                ]
            },
        }
    ]


async def test_handle_rpc_json_line_sets_provider_secret(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.set_provider_secret",
        lambda provider, secret: (
            captured.update({"provider": provider, "secret": secret})
            or ProviderAuthStatus(
                provider=provider,
                configured=True,
                source="keychain",
            )
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-set",
            "command": "auth.set",
            "payload": {
                "provider": "github",
                "secret": "test-token",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {"provider": "github", "secret": "test-token"}
    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-auth-set",
            "response": {
                "status": {
                    "provider": "github",
                    "configured": True,
                    "source": "keychain",
                }
            },
        }
    ]


async def test_handle_rpc_json_line_clears_provider_secret(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.clear_provider_secret",
        lambda provider: (
            captured.update({"provider": provider})
            or ProviderAuthStatus(
                provider=provider,
                configured=False,
                source="none",
            )
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-clear",
            "command": "auth.clear",
            "payload": {
                "provider": "openai",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {"provider": "openai"}
    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-auth-clear",
            "response": {
                "status": {
                    "provider": "openai",
                    "configured": False,
                    "source": "none",
                }
            },
        }
    ]


async def test_handle_rpc_json_line_compacts_session_and_returns_metadata(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    model = FunctionModel(
        function=compaction_summary_function,
        stream_function=resume_aware_write_stream,
    )

    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await _rpc_messages(
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

    messages = await _rpc_messages(
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
                "summarized_through_run_id": created_run_id,
                "first_kept_run_id": None,
                "summary": {
                    "current_objective": "finish note handling",
                    "established_facts": ["note.txt was created"],
                    "user_preferences": ["be concise"],
                    "important_paths": ["note.txt"],
                    "read_paths": [],
                    "modified_paths": ["note.txt"],
                    "open_questions": ["Should we add logging?"],
                    "unresolved_work": ["Run the final verifier."],
                },
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
    assert loaded.latest_compaction.first_kept_run_id is None


async def test_handle_rpc_json_line_returns_invalid_session_for_empty_compaction(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await _rpc_messages(
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
    session_id = await _create_session_id(
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
    ):
        captured["thinking"] = thinking
        captured["prompt"] = prompt
        yield {"type": "run_started", "run_id": "run-1"}
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    messages = await _rpc_messages(
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
    assert [message["event"]["type"] for message in messages] == [
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
    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-2",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "go"},
        },
        model=FunctionModel(stream_function=looping_edit_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert [message["type"] for message in messages] == ["rpc_event"] * 9
    assert [message["event"]["type"] for message in messages] == [
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
    assert messages[2]["event"]["result"] == {
        "ok": False,
        "error_type": "ToolMatchError",
        "message": (
            "old_text must match exactly once in "
            f"{workspace_root / 'note.txt'}; found 0 occurrences"
        ),
    }
    assert messages[4]["event"]["result"] == messages[2]["event"]["result"]
    assert messages[6]["event"]["result"] == messages[2]["event"]["result"]
    assert messages[-2]["event"]["delta"] == "done"
    assert messages[-1]["event"]["output_text"] == "done"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
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
        "assistant_text_delta",
        "run_succeeded",
    ]


async def test_handle_rpc_json_line_returns_unknown_session_error(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await _rpc_messages(
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

    session_id = await _create_session_id(
        workspace_root=first_workspace_root,
        sessions_root=sessions_root,
    )

    messages = await _rpc_messages(
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
            "error_type": "InvalidSession",
            "message": (
                "Session workspace_root mismatch: "
                f"expected {second_workspace_root.resolve()}, got "
                f"{first_workspace_root.resolve()}"
            ),
        }
    ]


async def test_handle_rpc_json_line_returns_internal_error_for_unexpected_exception(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        exploding_session_stream,
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-internal",
            "command": "run.start",
            "payload": {"session_id": session_id, "prompt": "go"},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-internal",
            "error_type": "InternalError",
            "message": "internal boom",
        }
    ]


async def test_handle_rpc_json_line_returns_invalid_json_error(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line="{",
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        )
    ]

    assert messages == [
        {
            "type": "rpc_error",
            "id": None,
            "error_type": "InvalidJSON",
            "message": "Invalid JSON request",
        }
    ]
    assert not sessions_root.exists()


@pytest.mark.parametrize(
    ("request_payload", "expected_id"),
    [
        (
            {
                "id": "req-3",
                "command": "run.nope",
                "payload": {"prompt": "go"},
            },
            "req-3",
        ),
        (
            {
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go"},
            },
            None,
        ),
        (
            {
                "id": 3,
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go"},
            },
            None,
        ),
        (
            {
                "id": "req-4",
                "payload": {"prompt": "go"},
            },
            "req-4",
        ),
        (
            {
                "id": "req-5",
                "command": "run.start",
            },
            "req-5",
        ),
        (
            {
                "id": "req-6",
                "command": "run.start",
                "payload": "go",
            },
            "req-6",
        ),
        (
            {
                "id": "req-7",
                "command": "run.start",
                "payload": {},
            },
            "req-7",
        ),
        (
            {
                "id": "req-8",
                "command": "run.start",
                "payload": {"prompt": "go"},
            },
            "req-8",
        ),
        (
            {
                "id": "req-9",
                "command": "run.start",
                "payload": {"session_id": 7, "prompt": "go"},
            },
            "req-9",
        ),
        (
            {
                "id": "req-10",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": 7},
            },
            "req-10",
        ),
        (
            {
                "id": "req-11",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go"},
                "extra": True,
            },
            "req-11",
        ),
        (
            {
                "id": "req-12",
                "command": "run.start",
                "payload": {"session_id": "s", "prompt": "go", "extra": True},
            },
            "req-12",
        ),
        (
            {
                "id": "req-12b",
                "command": "run.start",
                "payload": {
                    "session_id": "0" * 32,
                    "prompt": "go",
                    "thinking": "extreme",
                },
            },
            "req-12b",
        ),
        (
            {
                "id": "req-13",
                "command": "session.create",
                "payload": {"extra": True},
            },
            "req-13",
        ),
        (
            {
                "id": "req-14",
                "command": "session.create",
            },
            "req-14",
        ),
        (
            [],
            None,
        ),
    ],
)
async def test_handle_rpc_json_line_returns_invalid_request_error(
    tmp_path,
    request_payload: object,
    expected_id: str | None,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await _rpc_messages(
        request_payload=request_payload,
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": expected_id,
            "error_type": "InvalidRequest",
            "message": "Invalid RPC request",
        }
    ]
    assert not sessions_root.exists()
