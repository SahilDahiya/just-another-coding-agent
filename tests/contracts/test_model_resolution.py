import os

import pytest
from pydantic_ai.models import infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.retries import AsyncTenacityTransport

from just_another_coding_agent.contracts.model_catalog import (
    default_model_for_provider,
    shipped_models,
)
from just_another_coding_agent.runtime.models import (
    DEFAULT_OLLAMA_BASE_URL,
    build_canonical_model_settings,
    get_model_context_window_tokens,
    resolve_canonical_model,
    unwrap_instrumented_model,
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


def test_resolve_canonical_model_builds_explicit_google_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    model = resolve_canonical_model("google:gemini-2.5-flash")
    unwrapped = unwrap_instrumented_model(model)

    assert isinstance(unwrapped, GoogleModel)
    assert model.model_name == "gemini-2.5-flash"
    assert model.system == "google-gla"
    assert isinstance(model._provider, GoogleProvider)


def test_resolve_canonical_model_builds_explicit_openrouter_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    model = resolve_canonical_model("openrouter:anthropic/claude-sonnet-4-5")
    unwrapped = unwrap_instrumented_model(model)

    assert isinstance(unwrapped, OpenRouterModel)
    assert model.model_name == "anthropic/claude-sonnet-4-5"
    assert model.system == "openrouter"
    assert isinstance(model._provider, OpenRouterProvider)
    assert model._provider.base_url == "https://openrouter.ai/api/v1"


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

def test_build_canonical_model_settings_enable_parallel_tool_calls_for_supported_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = infer_model("anthropic:claude-3-5-haiku-latest")

    assert isinstance(model, AnthropicModel)
    assert build_canonical_model_settings(model=model) == {"parallel_tool_calls": True}


def test_build_canonical_model_settings_enable_parallel_tool_calls_for_ollama() -> None:
    model = OpenAIChatModel(
        "glm-5:cloud",
        provider=OllamaProvider(base_url="https://example.test/v1", api_key="test-key"),
    )

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


def test_resolve_canonical_model_uses_retrying_ollama_http_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("ollama:glm-5:cloud")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    client = unwrapped._provider.client

    assert isinstance(unwrapped, OpenAIChatModel)
    assert isinstance(model._provider, OllamaProvider)
    assert client.max_retries == 0
    assert isinstance(client._client._transport, AsyncTenacityTransport)


def test_resolve_canonical_model_uses_default_ollama_base_url_when_unset(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    model = resolve_canonical_model("ollama:glm-5:cloud")
    # Unwrap instrumentation to check the underlying model type
    unwrapped = unwrap_instrumented_model(model)
    assert isinstance(unwrapped, OpenAIChatModel)
    assert isinstance(model._provider, OllamaProvider)
    assert model._provider.base_url == f"{DEFAULT_OLLAMA_BASE_URL}/"


def test_resolve_canonical_model_rejects_missing_openrouter_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OpenRouter is not ready"):
        resolve_canonical_model("openrouter:anthropic/claude-sonnet-4-5")


def test_resolve_canonical_model_rejects_missing_hosted_openai_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    with pytest.raises(RuntimeError, match="OpenAI is not ready"):
        resolve_canonical_model("openai-responses:gpt-5.3-codex")


def test_resolve_canonical_model_rejects_missing_hosted_ollama_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")

    with pytest.raises(RuntimeError, match="Ollama cloud is not ready"):
        resolve_canonical_model("ollama:glm-5:cloud")


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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    assert get_model_context_window_tokens("openai-responses:gpt-5.3-codex") == 400_000
    assert get_model_context_window_tokens("openai-responses:gpt-5.4") == 1_050_000
    assert get_model_context_window_tokens("openai-responses:gpt-5.4-mini") == 400_000
    assert get_model_context_window_tokens("openai:gpt-5.4") == 1_050_000
    assert get_model_context_window_tokens("openai:gpt-5.4-mini") == 400_000
    assert get_model_context_window_tokens("openai:gpt-4o") == 128_000
    assert (
        get_model_context_window_tokens("openrouter:anthropic/claude-sonnet-4-5")
        == 200_000
    )
    assert get_model_context_window_tokens("anthropic:claude-sonnet-4-5") == 200_000
    assert get_model_context_window_tokens("anthropic:claude-haiku-4-5") == 200_000
    assert (
        get_model_context_window_tokens("anthropic:claude-haiku-4-5-20251001")
        == 200_000
    )
    assert get_model_context_window_tokens("anthropic:claude-opus-4-1") == 200_000
    assert get_model_context_window_tokens("google:gemini-2.5-flash") == 1_048_576
    assert get_model_context_window_tokens("google:gemini-2.5-flash-lite") == 1_048_576
    assert get_model_context_window_tokens("google:gemini-2.5-pro") == 1_048_576
    assert get_model_context_window_tokens("ollama:glm-5:cloud") == 198_000
    assert get_model_context_window_tokens("ollama:gemma4:e4b") == 128_000
    assert get_model_context_window_tokens("ollama:kimi-k2:1t-cloud") == 262_144
    assert get_model_context_window_tokens("ollama:qwen3.5:397b-cloud") == 262_144
    assert get_model_context_window_tokens("ollama:qwen3-coder-next") == 262_144

def test_all_backend_owned_shipped_models_have_context_windows() -> None:
    missing = sorted(
        model.model_id
        for model in shipped_models()
        if get_model_context_window_tokens(model.model_id) is None
    )

    assert missing == []


def test_backend_owned_default_model_per_provider_has_context_window() -> None:
    for provider in ("ollama", "openai", "openrouter", "anthropic", "google"):
        assert get_model_context_window_tokens(default_model_for_provider(provider))
