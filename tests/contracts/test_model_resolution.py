import os
from types import SimpleNamespace

import pytest
from pydantic_ai.models import infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport

from just_another_coding_agent.contracts.model_catalog import (
    default_model_for_provider,
    shipped_models,
)
from just_another_coding_agent.provider_readiness import compute_model_readiness
from just_another_coding_agent.runtime.models import (
    build_canonical_model_settings,
    get_model_context_window_tokens,
    resolve_canonical_model,
    unwrap_instrumented_model,
)
from just_another_coding_agent.runtime.turn_context import (
    build_session_turn_context_entry,
)


def test_resolve_canonical_model_keeps_model_instances() -> None:
    model = FunctionModel(function=lambda _messages, _info: "")

    resolved = resolve_canonical_model(model)
    # Unwrap instrumentation for identity check since tracing may be enabled
    assert unwrap_instrumented_model(resolved) is model


def test_resolve_canonical_model_builds_explicit_openai_responses_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert model.model_name == "gpt-5.3-codex"
    assert model.system == "openai"
    assert model._provider.base_url == "https://example.test/v1/"


def test_resolve_canonical_model_builds_explicit_openai_chat_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai:gpt-4o")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIChatModel)
    assert model.model_name == "gpt-4o"
    assert model.system == "openai"
    assert model._provider.base_url == "https://example.test/v1/"


def test_resolve_canonical_model_falls_back_to_pydanticai_for_other_strings() -> None:
    resolved = resolve_canonical_model("test")
    inferred = infer_model("test")

    # Unwrap instrumentation to check the underlying model type
    unwrapped_resolved = unwrap_instrumented_model(resolved)
    assert type(unwrapped_resolved) is type(inferred)
    assert resolved.model_name == inferred.model_name


def test_resolve_canonical_model_uses_env_defaults_when_base_url_is_unset(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert model._provider.base_url == os.environ.get(
        "OPENAI_BASE_URL",
        "https://api.openai.com/v1/",
    )


def test_compute_model_readiness_uses_oauth_for_openai_codex(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.oauth_store.OAUTH_FILE_PATH",
        tmp_path / "oauth.json",
    )
    monkeypatch.setattr(
        "just_another_coding_agent.provider_readiness.get_openai_codex_credentials",
        lambda: object(),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = compute_model_readiness("openai-responses:gpt-5-codex")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.requires_secret is False
    assert status.reason == "ok"


def test_compute_model_readiness_uses_oauth_for_openai_chatgpt_variant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.provider_readiness.get_openai_codex_credentials",
        lambda: object(),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = compute_model_readiness("openai-responses:gpt-5.4-chatgpt")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.requires_secret is False
    assert status.reason == "ok"


def test_compute_model_readiness_uses_env_oauth_for_openai_chatgpt_variant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.provider_readiness.get_openai_codex_credentials",
        lambda: None,
    )
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCESS_TOKEN", "access-token")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_EXPIRES_AT", "1776391632970")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCOUNT_ID", "acct-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = compute_model_readiness("openai-responses:gpt-5.4-chatgpt")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.requires_secret is False
    assert status.reason == "ok"


@pytest.mark.asyncio
async def test_resolve_canonical_model_builds_openai_codex_inside_running_loop(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    model = resolve_canonical_model("openai-responses:gpt-5-codex")

    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert model.model_name == "gpt-5-codex"
    assert model.system == "openai"
    assert str(model._provider.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert model._provider.client.default_headers["chatgpt-account-id"] == "acct-123"
    assert (
        model._provider.client.default_headers["OpenAI-Beta"]
        == "responses=experimental"
    )


def test_resolve_canonical_model_builds_openai_chatgpt_variant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    model = resolve_canonical_model("openai-responses:gpt-5.4-chatgpt")

    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert model.model_name == "gpt-5.4"
    assert model.system == "openai"
    assert str(model._provider.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert model._provider.client.default_headers["chatgpt-account-id"] == "acct-123"
    assert (
        model._provider.client.default_headers["OpenAI-Beta"]
        == "responses=experimental"
    )


def test_build_session_turn_context_entry_preserves_chatgpt_model_identity(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=resolve_canonical_model("openai-responses:gpt-5.4-chatgpt"),
        workspace_root=tmp_path,
    )

    assert entry.model == "openai-responses:gpt-5.4-chatgpt"


def test_resolve_canonical_model_refreshes_expired_openai_codex_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    model = resolve_canonical_model("openai-responses:gpt-5-codex")

    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert model._provider.client.api_key == "oauth-access"


def test_build_canonical_model_settings_sets_openai_store_false_for_codex(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    settings = build_canonical_model_settings(
        model="openai-responses:gpt-5-codex"
    )

    assert settings is not None
    assert settings["openai_store"] is False
    assert settings["parallel_tool_calls"] is True


def test_build_canonical_model_settings_sets_openai_store_false_for_chatgpt_variant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    settings = build_canonical_model_settings(
        model="openai-responses:gpt-5.4-chatgpt"
    )

    assert settings is not None
    assert settings["openai_store"] is False
    assert settings["parallel_tool_calls"] is True


def test_resolve_canonical_model_rejects_removed_suffix_variant(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(
        ValueError,
        match=r"unsupported model id: gpt-5\.4-copilot",
    ):
        resolve_canonical_model("openai-responses:gpt-5.4-copilot")


def test_build_canonical_model_settings_merge_model_defaults() -> None:
    model = OpenAIResponsesModel(
        "gpt-5.3-codex",
        provider=OpenAIProvider(base_url="https://example.test/v1", api_key="test-key"),
        settings=OpenAIResponsesModelSettings(openai_previous_response_id="auto"),
    )

    assert build_canonical_model_settings(model=model, thinking="high") == {
        "openai_previous_response_id": "auto",
        "parallel_tool_calls": True,
        "thinking": "high",
    }


def test_build_canonical_model_settings_strips_previous_response_id_for_codex(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )
    model = OpenAIResponsesModel(
        "gpt-5-codex",
        provider=OpenAIProvider(
            openai_client=SimpleNamespace(
                base_url="https://chatgpt.com/backend-api/codex/",
            )
        ),
        settings=OpenAIResponsesModelSettings(openai_previous_response_id="auto"),
    )

    settings = build_canonical_model_settings(model=model)

    assert settings is not None
    assert settings["openai_store"] is False
    assert "openai_previous_response_id" not in settings


def test_build_canonical_model_settings_enable_parallel_tool_calls_for_supported_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = infer_model("anthropic:claude-3-5-haiku-latest")

    assert isinstance(model, AnthropicModel)
    assert build_canonical_model_settings(model=model) == {"parallel_tool_calls": True}


def test_resolve_canonical_model_uses_retrying_openai_http_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    client = unwrapped._provider.client

    assert isinstance(unwrapped, OpenAIResponsesModel)
    assert client.max_retries == 0
    assert isinstance(client._client._transport, AsyncTenacityTransport)


def test_resolve_canonical_model_rejects_missing_hosted_openai_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "auth.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    with pytest.raises(RuntimeError, match="OpenAI is not ready"):
        resolve_canonical_model("openai-responses:gpt-5.3-codex")


def test_resolve_canonical_model_wraps_with_instrumentation_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "local")
    model = FunctionModel(function=lambda _messages, _info: "")

    resolved = resolve_canonical_model(model)

    assert isinstance(resolved, InstrumentedModel)
    assert resolved.wrapped is model

def test_build_canonical_model_settings_unwraps_instrumented_models() -> None:
    model = InstrumentedModel(
        OpenAIResponsesModel(
            "gpt-5.3-codex",
            provider=OpenAIProvider(
                base_url="https://example.test/v1",
                api_key="test-key",
            ),
        )
    )

    assert build_canonical_model_settings(model=model) == {"parallel_tool_calls": True}


def test_get_model_context_window_tokens_for_supported_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert get_model_context_window_tokens("openai-responses:gpt-5.3-codex") == 400_000
    assert get_model_context_window_tokens("openai-responses:gpt-5.4") == 1_050_000
    assert get_model_context_window_tokens("openai-responses:gpt-5.4-mini") == 400_000
    assert get_model_context_window_tokens("openai-responses:gpt-5-codex") == 400_000
    assert (
        get_model_context_window_tokens("openai-responses:gpt-5.4-chatgpt")
        == 400_000
    )
    assert (
        get_model_context_window_tokens("openai-responses:gpt-5.1-codex-chatgpt")
        == 400_000
    )
    assert get_model_context_window_tokens("openai:gpt-5.4") == 1_050_000
    assert get_model_context_window_tokens("openai:gpt-5.4-mini") == 400_000
    assert get_model_context_window_tokens("openai:gpt-4o") == 128_000
    assert get_model_context_window_tokens("anthropic:claude-sonnet-4-5") == 200_000
    assert get_model_context_window_tokens("anthropic:claude-haiku-4-5") == 200_000
    assert (
        get_model_context_window_tokens("anthropic:claude-haiku-4-5-20251001")
        == 200_000
    )
    assert get_model_context_window_tokens("anthropic:claude-opus-4-1") == 200_000


def test_get_model_context_window_tokens_preserves_chatgpt_context_window_for_resolved_model(  # noqa: E501
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.models.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-access", account_id="acct-123"),
    )

    model = resolve_canonical_model("openai-responses:gpt-5.4-chatgpt")

    assert get_model_context_window_tokens(model) == 400_000

def test_get_model_context_window_tokens_returns_none_for_removed_suffix_models(
) -> None:
    assert get_model_context_window_tokens("openai-responses:gpt-5.4-copilot") is None
    assert (
        get_model_context_window_tokens("anthropic:claude-sonnet-4.5-copilot")
        is None
    )

def test_all_backend_owned_shipped_models_have_context_windows() -> None:
    missing = sorted(
        model.model_id
        for model in shipped_models()
        if get_model_context_window_tokens(model.model_id) is None
    )

    assert missing == []


def test_backend_owned_default_model_per_provider_has_context_window() -> None:
    for provider in ("openai", "anthropic"):
        assert get_model_context_window_tokens(default_model_for_provider(provider))
