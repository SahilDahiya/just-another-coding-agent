import asyncio
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.auth import AuthStoreError, OpenAICodexLoginFlow
from just_another_coding_agent.contracts.auth import (
    OAuthProviderStatus,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import (
    CANONICAL_PROVIDER_ORDER,
    default_model_for_provider,
    shipped_models_for_provider,
)
from just_another_coding_agent.oauth_openai_codex import start_openai_codex_login
from just_another_coding_agent.rpc.handlers.auth import _prune_stale_login_flows
from just_another_coding_agent.rpc.state import _OpenAICodexLoginFlowState
from tests.contracts.rpc_stdio_test_support import (
    rpc_messages,
    text_only_stream,
)


def test_openai_codex_login_redirect_host_matches_callback_listener() -> None:
    _flow, start = start_openai_codex_login()

    parsed = urlparse(start.auth_url)
    redirect_uri = parse_qs(parsed.query)["redirect_uri"][0]
    redirect = urlparse(redirect_uri)

    assert redirect.hostname == "localhost"
    assert redirect.port == 1455


async def test_handle_rpc_json_line_returns_backend_owned_model_catalog(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await rpc_messages(
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
        "just_another_coding_agent.rpc.handlers.auth.list_provider_auth_statuses",
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
        "just_another_coding_agent.rpc.handlers.auth.get_local_secret_store_status",
        lambda: {
            "available": True,
            "message": None,
            "file_store_path": "/tmp/jaca-auth.json",
        },
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.get_oauth_provider_statuses",
        lambda: [
            {
                "provider": "openai-codex",
                "logged_in": True,
                "account_id": "acct-123",
                "expires_at": 1760000000000,
            },
        ],
    )

    messages = await rpc_messages(
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
                    }
                ],
            },
        }
    ]


async def test_handle_rpc_json_line_returns_logfire_trace_status(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.logfire_setup_status",
        lambda: SimpleNamespace(installed=False, credentials_configured=False),
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-trace-logfire-status",
            "command": "trace.logfire_status",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-trace-logfire-status",
            "response": {
                "installed": False,
                "credentials_configured": False,
            },
        }
    ]


async def test_handle_rpc_json_line_starts_openai_codex_login(
    tmp_path,
    monkeypatch,
    rpc_runtime_state,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.start_openai_codex_oauth_login",
        lambda: (
            OpenAICodexLoginFlow(flow_id="flow-1", verifier="v", state="s"),
            "flow-1",
            "https://auth.example.test/login",
            (
                "If JACA does not finish automatically, paste the one-time code "
                "shown in the browser here."
            ),
        ),
    )
    gate = asyncio.Event()

    async def _wait(_flow):
        await gate.wait()

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.wait_for_openai_codex_oauth_login",
        _wait,
    )

    messages = await rpc_messages(
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
                "instructions": (
                    "If JACA does not finish automatically, paste the one-time "
                    "code shown in the browser here."
                ),
            },
        }
    ]
    assert "flow-1" in rpc_runtime_state.openai_codex_login_flows
    started_state = rpc_runtime_state.openai_codex_login_flows["flow-1"]
    assert started_state.task is not None
    await asyncio.sleep(0)
    started_state.task.cancel()
    await asyncio.gather(started_state.task, return_exceptions=True)


async def test_handle_rpc_json_line_replaces_existing_openai_codex_login_flow(
    tmp_path,
    monkeypatch,
    rpc_runtime_state,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    issued_flows = iter(
        [
            (
                OpenAICodexLoginFlow(flow_id="flow-1", verifier="v1", state="s1"),
                "flow-1",
                "https://auth.example.test/login-1",
                "Paste code 1.",
            ),
            (
                OpenAICodexLoginFlow(flow_id="flow-2", verifier="v2", state="s2"),
                "flow-2",
                "https://auth.example.test/login-2",
                "Paste code 2.",
            ),
        ]
    )
    cancelled: list[str] = []

    async def _wait(flow: OpenAICodexLoginFlow):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(flow.flow_id)
            raise

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.start_openai_codex_oauth_login",
        lambda: next(issued_flows),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.wait_for_openai_codex_oauth_login",
        _wait,
    )

    await rpc_messages(
        request_payload={
            "id": "req-login-start-1",
            "command": "auth.login_openai_codex.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    first_task = rpc_runtime_state.openai_codex_login_flows["flow-1"].task
    assert first_task is not None
    await asyncio.sleep(0)

    await rpc_messages(
        request_payload={
            "id": "req-login-start-2",
            "command": "auth.login_openai_codex.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await asyncio.gather(first_task, return_exceptions=True)

    assert first_task.cancelled()
    assert cancelled == ["flow-1"]
    assert list(rpc_runtime_state.openai_codex_login_flows) == ["flow-2"]
    assert rpc_runtime_state.openai_codex_login_flows["flow-2"].task is not None
    assert rpc_runtime_state.openai_codex_login_flows["flow-2"].result is not None
    assert rpc_runtime_state.openai_codex_login_flows["flow-2"].started_at is not None

    rpc_runtime_state.openai_codex_login_flows["flow-2"].task.cancel()
    await asyncio.gather(
        *[
            state.task
            for state in rpc_runtime_state.openai_codex_login_flows.values()
            if state.task is not None
        ],
        return_exceptions=True,
    )


async def test_handle_rpc_json_line_preserves_login_flow_after_failed_completion(
    tmp_path,
    monkeypatch,
    rpc_runtime_state,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    flow = OpenAICodexLoginFlow(flow_id="flow-2", verifier="v", state="s")
    rpc_runtime_state.openai_codex_login_flows["flow-2"] = _OpenAICodexLoginFlowState(
        flow=flow,
        result=asyncio.get_running_loop().create_future(),
        started_at=time.monotonic(),
    )
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
        "just_another_coding_agent.rpc.handlers.auth.complete_openai_codex_oauth_login",
        _complete,
    )

    bad_messages = await rpc_messages(
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
    assert "flow-2" in rpc_runtime_state.openai_codex_login_flows

    good_messages = await rpc_messages(
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
    assert "flow-2" not in rpc_runtime_state.openai_codex_login_flows
    assert attempt_counter["count"] == 2


async def test_handle_rpc_json_line_waits_for_openai_codex_login(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    gate = asyncio.Event()

    async def _wait(_flow):
        await gate.wait()
        return OAuthProviderStatus(
            provider="openai-codex",
            logged_in=True,
            account_id="acct-123",
            expires_at=1760000000000,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.wait_for_openai_codex_oauth_login",
        _wait,
    )

    start_messages = await rpc_messages(
        request_payload={
            "id": "req-login-start-real",
            "command": "auth.login_openai_codex.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    flow_id = str(start_messages[0]["response"]["flow_id"])

    wait_task = asyncio.create_task(
        rpc_messages(
            request_payload={
                "id": "req-login-wait",
                "command": "auth.login_openai_codex.wait",
                "payload": {"flow_id": flow_id},
            },
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        )
    )

    await asyncio.sleep(0)
    assert not wait_task.done()

    gate.set()
    wait_messages = await wait_task

    assert wait_messages == [
        {
            "type": "rpc_response",
            "id": "req-login-wait",
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


async def test_handle_rpc_json_line_wait_openai_codex_resolves_after_manual_completion(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    async def _wait(_flow):
        await asyncio.Event().wait()

    async def _complete(_flow, callback_or_code: str):
        assert callback_or_code == "good-code"
        return OAuthProviderStatus(
            provider="openai-codex",
            logged_in=True,
            account_id="acct-123",
            expires_at=1760000000000,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.wait_for_openai_codex_oauth_login",
        _wait,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.complete_openai_codex_oauth_login",
        _complete,
    )

    start_messages = await rpc_messages(
        request_payload={
            "id": "req-login-start-real",
            "command": "auth.login_openai_codex.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    flow_id = str(start_messages[0]["response"]["flow_id"])

    wait_task = asyncio.create_task(
        rpc_messages(
            request_payload={
                "id": "req-login-wait",
                "command": "auth.login_openai_codex.wait",
                "payload": {"flow_id": flow_id},
            },
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        )
    )

    await asyncio.sleep(0)
    assert not wait_task.done()

    complete_messages = await rpc_messages(
        request_payload={
            "id": "req-login-good",
            "command": "auth.login_openai_codex.complete",
            "payload": {
                "flow_id": flow_id,
                "callback_or_code": "good-code",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    wait_messages = await wait_task

    expected_response = {
        "status": {
            "provider": "openai-codex",
            "logged_in": True,
            "account_id": "acct-123",
            "expires_at": 1760000000000,
        }
    }
    assert complete_messages == [
        {
            "type": "rpc_response",
            "id": "req-login-good",
            "response": expected_response,
        }
    ]
    assert wait_messages == [
        {
            "type": "rpc_response",
            "id": "req-login-wait",
            "response": expected_response,
        }
    ]


async def test_handle_rpc_json_line_openai_wait_unknown_flow_returns_logged_in_status(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.get_oauth_provider_statuses",
        lambda: [
            OAuthProviderStatus(
                provider="openai-codex",
                logged_in=True,
                account_id="acct-123",
                expires_at=1760000000000,
            )
        ],
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-login-wait",
            "command": "auth.login_openai_codex.wait",
            "payload": {"flow_id": "missing-flow"},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-login-wait",
            "response": {
                "status": {
                    "provider": "openai-codex",
                    "logged_in": True,
                    "account_id": "acct-123",
                    "expires_at": 1760000000000,
                },
            },
        }
    ]


def test_prune_stale_login_flows_removes_expired_openai_codex_entries(
    rpc_runtime_state,
) -> None:
    flow = OpenAICodexLoginFlow(flow_id="stale-flow", verifier="v", state="s")
    rpc_runtime_state.openai_codex_login_flows["stale-flow"] = (
        _OpenAICodexLoginFlowState(
            flow=flow,
            started_at=0.0,
        )
    )

    _prune_stale_login_flows(now=10_000.0)

    assert "stale-flow" not in rpc_runtime_state.openai_codex_login_flows


@pytest.mark.asyncio
async def test_prune_stale_login_flows_keeps_completed_openai_task_until_waited(
    rpc_runtime_state,
) -> None:
    flow = OpenAICodexLoginFlow(flow_id="done-flow", verifier="v", state="s")
    task = asyncio.create_task(asyncio.sleep(0, result="done"))
    await task

    rpc_runtime_state.openai_codex_login_flows["done-flow"] = (
        _OpenAICodexLoginFlowState(
            flow=flow,
            task=task,
            started_at=9_999.0,
        )
    )

    _prune_stale_login_flows(now=10_000.0)

    assert "done-flow" in rpc_runtime_state.openai_codex_login_flows
    assert rpc_runtime_state.openai_codex_login_flows["done-flow"].task is task
    assert rpc_runtime_state.openai_codex_login_flows["done-flow"].result is None
    assert (
        rpc_runtime_state.openai_codex_login_flows["done-flow"].started_at == 9_999.0
    )


async def test_handle_rpc_json_line_sets_provider_secret(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.set_provider_secret",
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

    messages = await rpc_messages(
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


async def test_handle_rpc_json_line_prepares_auth_file(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.handlers.auth.prepare_provider_secret_file",
        lambda provider: type(
            "PreparedAuthFile",
            (),
            {
                "provider": provider,
                "env_key": "OPENAI_API_KEY",
                "file_path": "/tmp/auth.json",
                "created": True,
                "file_snippet": '{\n  "OPENAI_API_KEY": "..."\n}',
                "entry_snippet": '"OPENAI_API_KEY": "..."',
            },
        )(),
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-auth-prepare",
            "command": "auth.prepare_file",
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
            "type": "rpc_response",
            "id": "req-auth-prepare",
            "response": {
                "provider": "openai",
                "env_key": "OPENAI_API_KEY",
                "file_path": "/tmp/auth.json",
                "created": True,
                "file_snippet": '{\n  "OPENAI_API_KEY": "..."\n}',
                "entry_snippet": '"OPENAI_API_KEY": "..."',
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
        "just_another_coding_agent.rpc.handlers.auth.clear_provider_secret",
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

    messages = await rpc_messages(
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

    messages = await rpc_messages(
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
        "just_another_coding_agent.rpc.handlers.auth.list_provider_auth_statuses",
        lambda: (_ for _ in ()).throw(AuthStoreError("auth store unavailable")),
    )

    messages = await rpc_messages(
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
        "just_another_coding_agent.rpc.handlers.auth.clear_provider_secret",
        lambda _provider: (_ for _ in ()).throw(
            AuthStoreError("auth store unavailable")
        ),
    )

    messages = await rpc_messages(
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
