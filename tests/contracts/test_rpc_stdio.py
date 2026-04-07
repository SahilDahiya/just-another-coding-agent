import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.auth import (
    AuthStoreError,
    GitHubCopilotLoginFlow,
    OpenAICodexLoginFlow,
)
from just_another_coding_agent.contracts.auth import ProviderAuthStatus
from just_another_coding_agent.contracts.model_catalog import (
    CANONICAL_PROVIDER_ORDER,
    default_model_for_provider,
    shipped_models_for_provider,
)
from just_another_coding_agent.oauth_openai_codex import start_openai_codex_login
from just_another_coding_agent.rpc.session_store import (
    create_session,
    session_path_for_id,
)
from just_another_coding_agent.rpc.stdio import (
    _GITHUB_COPILOT_LOGIN_FLOWS,
    _GITHUB_COPILOT_LOGIN_STARTED_AT,
    _OPENAI_CODEX_LOGIN_FLOWS,
    _OPENAI_CODEX_LOGIN_STARTED_AT,
    _FollowUpState,
    _prune_stale_login_flows,
    handle_rpc_json_line,
)
from just_another_coding_agent.session import load_session


async def _noop_emit_queue_state(_event) -> None:
    return None


async def _noop_emit_rpc_event(_request_id: str, _event) -> None:
    return None


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
    assert "Primary intent:" in prompt
    assert "- create note" in prompt
    assert "Current state:" in prompt
    assert "Completed work:" in prompt
    assert "Tool evidence:" in prompt
    assert "create note" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content="\n".join(
                    [
                        "Primary Intent:",
                        "- Create note handling and preserve prior file work.",
                        "Completed Work:",
                        "- note.txt was created.",
                        "Important Files/Paths:",
                        "- note.txt: created during the previous run.",
                        "Next Step:",
                        "- Run the final verifier.",
                        "Stable Preferences:",
                        "- Be concise.",
                    ]
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

    async def _emit_rpc_event(_request_id: str, _event) -> None:
        return None

    return [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=model,
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=_emit_rpc_event,
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
        workspace_root=workspace_root,
        session_id=session_id,
    )
    assert session_path.exists()
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs == []
    return session_id


async def test_follow_up_state_interrupt_promotes_pending_steer_to_front() -> None:
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    await state.activate(
        "a" * 32,
        run_task=run_task,
        emit_queue_state=_noop_emit_queue_state,
    )
    await state.enqueue("a" * 32, "later prompt", mode="later")
    await state.activate_steer_boundary("a" * 32, lambda prompts: None)
    await state.enqueue("a" * 32, "steer prompt", mode="next")

    promoted_count = await state.interrupt(
        "a" * 32,
        promote_queued_steer=True,
    )

    assert promoted_count == 1
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch("a" * 32) == ["steer prompt"]
    assert await state.take_next_follow_up_batch("a" * 32) == ["later prompt"]


def test_openai_codex_login_redirect_host_matches_callback_listener() -> None:
    _flow, start = start_openai_codex_login()

    parsed = urlparse(start.auth_url)
    redirect_uri = parse_qs(parsed.query)["redirect_uri"][0]
    redirect = urlparse(redirect_uri)

    assert redirect.hostname == "localhost"
    assert redirect.port == 1455


async def test_follow_up_state_interrupt_preserves_fifo_within_promoted_and_later(
) -> None:
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    session_id = "b" * 32
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=_noop_emit_queue_state,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.enqueue(session_id, "later two", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")
    await state.enqueue(session_id, "next two", mode="next")

    promoted_count = await state.interrupt(
        session_id,
        promote_queued_steer=True,
    )

    assert promoted_count == 2
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch(session_id) == [
        "next one",
        "next two",
    ]
    assert await state.take_next_follow_up_batch(session_id) == [
        "later one",
        "later two",
    ]


async def test_follow_up_state_downgrades_pending_next_ahead_of_existing_later(
) -> None:
    state = _FollowUpState()
    session_id = "c" * 32
    run_task = asyncio.create_task(asyncio.Event().wait())
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=_noop_emit_queue_state,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.enqueue(session_id, "later two", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")
    await state.enqueue(session_id, "next two", mode="next")

    await state.downgrade_pending_steers_to_follow_ups(session_id)

    assert await state.take_next_follow_up_batch(session_id) == [
        "next one",
        "next two",
    ]
    assert await state.take_next_follow_up_batch(session_id) == [
        "later one",
        "later two",
    ]
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task


async def test_follow_up_state_interrupt_without_promotion_preserves_later_only(
) -> None:
    state = _FollowUpState()
    session_id = "d" * 32
    run_task = asyncio.create_task(asyncio.Event().wait())
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=_noop_emit_queue_state,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")

    promoted_count = await state.interrupt(
        session_id,
        promote_queued_steer=False,
    )

    assert promoted_count == 0
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch(session_id) == ["later one"]
    assert await state.take_next_follow_up_batch(session_id) is None


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

    assert [message["type"] for message in first_messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
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

    assert [message["type"] for message in second_messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
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

    session_id = await _create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    await _rpc_messages(
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
    await _rpc_messages(
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

    messages = await _rpc_messages(
        request_payload={
            "id": "req-preview",
            "command": "session.preview",
            "payload": {"session_id": session_id},
        },
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-preview",
            "response": {
                "session_id": session_id,
                "entries": [
                    {"kind": "user", "text": "create note"},
                    {"kind": "assistant", "text": "created"},
                    {"kind": "user", "text": "what did you do?"},
                    {"kind": "assistant", "text": "I created note.txt"},
                ],
                "truncated": False,
            },
        }
    ]


async def test_handle_rpc_json_line_names_session_with_backend_normalization(
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
                        "provider": provider,
                        "default_model_id": default_model_for_provider(provider),
                        "models": [
                            {
                                "model_id": model.model_id,
                                "description": model.description,
                            }
                            for model in shipped_models_for_provider(provider)
                        ],
                    }
                    for provider in CANONICAL_PROVIDER_ORDER
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
            ProviderAuthStatus(
                provider="openai",
                configured=True,
                secret_configured=True,
                requires_secret=True,
                source="file",
                env_key="OPENAI_API_KEY",
                reason="ok",
            ),
            ProviderAuthStatus(
                provider="anthropic",
                configured=False,
                secret_configured=False,
                requires_secret=True,
                source="none",
                env_key="ANTHROPIC_API_KEY",
                reason="missing_secret",
            ),
        ],
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.get_local_secret_store_status",
        lambda: {
            "available": True,
            "message": None,
            "file_store_path": "/tmp/jaca-auth.json",
        },
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.get_oauth_provider_statuses",
        lambda: [
            {
                "provider": "openai-codex",
                "logged_in": True,
                "account_id": "acct-123",
                "expires_at": 1760000000000,
            },
            {
                "provider": "github-copilot",
                "logged_in": False,
                "account_id": None,
                "expires_at": None,
            },
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
                        "provider": "openai",
                        "configured": True,
                        "secret_configured": True,
                        "requires_secret": True,
                        "source": "file",
                        "env_key": "OPENAI_API_KEY",
                        "reason": "ok",
                    },
                    {
                        "provider": "anthropic",
                        "configured": False,
                        "secret_configured": False,
                        "requires_secret": True,
                        "source": "none",
                        "env_key": "ANTHROPIC_API_KEY",
                        "reason": "missing_secret",
                    },
                ],
                "local_secret_store": {
                    "available": True,
                    "message": None,
                    "file_store_path": "/tmp/jaca-auth.json",
                },
                "oauth_providers": [
                    {
                        "provider": "openai-codex",
                        "logged_in": True,
                        "account_id": "acct-123",
                        "expires_at": 1760000000000,
                    },
                    {
                        "provider": "github-copilot",
                        "logged_in": False,
                        "account_id": None,
                        "expires_at": None,
                    }
                ],
            },
        }
    ]


async def test_handle_rpc_json_line_starts_openai_codex_login(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _OPENAI_CODEX_LOGIN_FLOWS.clear()
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.start_openai_codex_oauth_login",
        lambda: (
            OpenAICodexLoginFlow(flow_id="flow-1", verifier="v", state="s"),
            "flow-1",
            "https://auth.example.test/login",
            "Paste the redirect URL or authorization code.",
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-login-start",
            "command": "auth.login_openai_codex.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-login-start",
            "response": {
                "flow_id": "flow-1",
                "auth_url": "https://auth.example.test/login",
                "instructions": "Paste the redirect URL or authorization code.",
            },
        }
    ]
    assert "flow-1" in _OPENAI_CODEX_LOGIN_FLOWS


async def test_handle_rpc_json_line_starts_github_copilot_login(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _GITHUB_COPILOT_LOGIN_FLOWS.clear()
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.start_github_copilot_oauth_login",
        lambda enterprise_domain=None: (
            GitHubCopilotLoginFlow(
                flow_id="flow-gh-1",
                domain="github.com",
                device_code="device-code",
                interval_seconds=5,
                expires_in_seconds=900,
                verification_uri="https://github.com/login/device",
                user_code="ABCD-EFGH",
            ),
            "flow-gh-1",
            "https://github.com/login/device",
            "Enter code: ABCD-EFGH",
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-gh-login-start",
            "command": "auth.login_github_copilot.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-gh-login-start",
            "response": {
                "flow_id": "flow-gh-1",
                "auth_url": "https://github.com/login/device",
                "instructions": "Enter code: ABCD-EFGH",
                "user_code": "ABCD-EFGH",
            },
        }
    ]
    assert "flow-gh-1" in _GITHUB_COPILOT_LOGIN_FLOWS


async def test_handle_rpc_json_line_preserves_login_flow_after_failed_completion(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _OPENAI_CODEX_LOGIN_FLOWS.clear()
    flow = OpenAICodexLoginFlow(flow_id="flow-2", verifier="v", state="s")
    _OPENAI_CODEX_LOGIN_FLOWS["flow-2"] = flow
    attempt_counter = {"count": 0}

    async def _complete(_flow, callback_or_code: str):
        attempt_counter["count"] += 1
        if callback_or_code == "bad-code":
            raise RuntimeError("invalid callback")
        return {
            "provider": "openai-codex",
            "logged_in": True,
            "account_id": "acct-123",
            "expires_at": 1760000000000,
        }

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.complete_openai_codex_oauth_login",
        _complete,
    )

    bad_messages = await _rpc_messages(
        request_payload={
            "id": "req-login-bad",
            "command": "auth.login_openai_codex.complete",
            "payload": {
                "flow_id": "flow-2",
                "callback_or_code": "bad-code",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert bad_messages == [
        {
            "type": "rpc_error",
            "id": "req-login-bad",
            "error_type": "InternalError",
            "message": "invalid callback",
        }
    ]
    assert "flow-2" in _OPENAI_CODEX_LOGIN_FLOWS

    good_messages = await _rpc_messages(
        request_payload={
            "id": "req-login-good",
            "command": "auth.login_openai_codex.complete",
            "payload": {
                "flow_id": "flow-2",
                "callback_or_code": "good-code",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert good_messages == [
        {
            "type": "rpc_response",
            "id": "req-login-good",
            "response": {
                "status": {
                    "provider": "openai-codex",
                    "logged_in": True,
                    "account_id": "acct-123",
                    "expires_at": 1760000000000,
                }
            },
        }
    ]
    assert "flow-2" not in _OPENAI_CODEX_LOGIN_FLOWS
    assert attempt_counter["count"] == 2


async def test_handle_rpc_json_line_polls_github_copilot_login(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _GITHUB_COPILOT_LOGIN_FLOWS.clear()
    flow = GitHubCopilotLoginFlow(
        flow_id="flow-gh-2",
        domain="github.com",
        device_code="device-code",
        interval_seconds=5,
        expires_in_seconds=900,
        verification_uri="https://github.com/login/device",
        user_code="ABCD-EFGH",
    )
    _GITHUB_COPILOT_LOGIN_FLOWS["flow-gh-2"] = flow

    async def _wait(_flow):
        return {
            "provider": "github-copilot",
            "logged_in": True,
            "account_id": None,
            "expires_at": 1760000000000,
        }

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.wait_for_github_copilot_oauth_login",
        _wait,
    )

    start_messages = await _rpc_messages(
        request_payload={
            "id": "req-gh-login-start-real",
            "command": "auth.login_github_copilot.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    flow_id = str(start_messages[0]["response"]["flow_id"])

    poll_messages = await _rpc_messages(
        request_payload={
            "id": "req-gh-login-poll",
            "command": "auth.login_github_copilot.poll",
            "payload": {"flow_id": flow_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert poll_messages[0]["type"] == "rpc_response"
    assert poll_messages[0]["response"]["done"] in {False, True}


def test_prune_stale_login_flows_removes_expired_openai_codex_entries() -> None:
    _OPENAI_CODEX_LOGIN_FLOWS.clear()
    _OPENAI_CODEX_LOGIN_STARTED_AT.clear()
    flow = OpenAICodexLoginFlow(flow_id="stale-flow", verifier="v", state="s")
    _OPENAI_CODEX_LOGIN_FLOWS["stale-flow"] = flow
    _OPENAI_CODEX_LOGIN_STARTED_AT["stale-flow"] = 0.0

    _prune_stale_login_flows(now=10_000.0)

    assert "stale-flow" not in _OPENAI_CODEX_LOGIN_FLOWS
    assert "stale-flow" not in _OPENAI_CODEX_LOGIN_STARTED_AT


def test_prune_stale_login_flows_removes_expired_github_copilot_entries() -> None:
    _GITHUB_COPILOT_LOGIN_FLOWS.clear()
    _GITHUB_COPILOT_LOGIN_STARTED_AT.clear()
    flow = GitHubCopilotLoginFlow(
        flow_id="stale-gh-flow",
        domain="github.com",
        device_code="device-code",
        interval_seconds=5,
        expires_in_seconds=900,
        verification_uri="https://github.com/login/device",
        user_code="ABCD-EFGH",
    )
    _GITHUB_COPILOT_LOGIN_FLOWS["stale-gh-flow"] = flow
    _GITHUB_COPILOT_LOGIN_STARTED_AT["stale-gh-flow"] = 0.0

    _prune_stale_login_flows(now=10_000.0)

    assert "stale-gh-flow" not in _GITHUB_COPILOT_LOGIN_FLOWS
    assert "stale-gh-flow" not in _GITHUB_COPILOT_LOGIN_STARTED_AT


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
        lambda provider, secret, storage: (
            captured.update(
                {"provider": provider, "secret": secret, "storage": storage}
            )
            or ProviderAuthStatus(
                provider=provider,
                configured=True,
                secret_configured=True,
                requires_secret=True,
                source="file",
                env_key="OPENAI_API_KEY",
                reason="ok",
            )
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-set",
            "command": "auth.set",
            "payload": {
                "provider": "openai",
                "secret": "test-token",
                "storage": "file",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {
        "provider": "openai",
        "secret": "test-token",
        "storage": "file",
    }
    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-auth-set",
            "response": {
                "status": {
                    "provider": "openai",
                    "configured": True,
                    "secret_configured": True,
                    "requires_secret": True,
                    "source": "file",
                    "env_key": "OPENAI_API_KEY",
                    "reason": "ok",
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
                secret_configured=False,
                requires_secret=True,
                source="none",
                env_key="OPENAI_API_KEY",
                reason="missing_secret",
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
                    "secret_configured": False,
                    "requires_secret": True,
                    "source": "none",
                    "env_key": "OPENAI_API_KEY",
                    "reason": "missing_secret",
                }
            },
        }
    ]


async def test_handle_rpc_json_line_rejects_blank_provider_secret_as_invalid_request(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-set-blank",
            "command": "auth.set",
            "payload": {
                "provider": "openai",
                "secret": "   ",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-auth-set-blank",
            "error_type": "InvalidRequest",
            "message": "provider secret must be a non-empty string",
        }
    ]


async def test_handle_rpc_json_line_returns_internal_error_for_auth_status_failure(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.list_provider_auth_statuses",
        lambda: (_ for _ in ()).throw(AuthStoreError("auth store unavailable")),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-status-fail",
            "command": "auth.status",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-auth-status-fail",
            "error_type": "InternalError",
            "message": "auth store unavailable",
        }
    ]


async def test_handle_rpc_json_line_returns_internal_error_for_auth_clear_store_failure(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.clear_provider_secret",
        lambda _provider: (_ for _ in ()).throw(
            AuthStoreError("auth store unavailable")
        ),
    )

    messages = await _rpc_messages(
        request_payload={
            "id": "req-auth-clear-fail",
            "command": "auth.clear",
            "payload": {
                "provider": "openai",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-auth-clear-fail",
            "error_type": "InternalError",
            "message": "auth store unavailable",
        }
    ]


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

    messages = await _rpc_messages(
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
            "error_type": "UnknownSession",
            "message": f"Unknown session_id: {session_id}",
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
            emit_rpc_event=_noop_emit_rpc_event,
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
