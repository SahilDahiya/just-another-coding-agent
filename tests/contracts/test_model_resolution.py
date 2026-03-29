import os

from pydantic_ai.models import infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport

from just_another_coding_agent.runtime.models import (
    DEFAULT_OLLAMA_BASE_URL,
    build_canonical_model_settings,
    build_in_run_compaction_soft_char_limit,
    get_model_context_window_tokens,
    resolve_canonical_model,
)


def test_resolve_canonical_model_keeps_model_instances() -> None:
    model = FunctionModel(function=lambda _messages, _info: "")

    assert resolve_canonical_model(model) is model


def test_resolve_canonical_model_builds_explicit_openai_responses_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "gpt-5.3-codex"
    assert model.system == "openai"
    assert model._provider.base_url == "https://example.test/v1/"


def test_resolve_canonical_model_builds_explicit_openai_chat_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai:gpt-4o")

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-4o"
    assert model.system == "openai"
    assert model._provider.base_url == "https://example.test/v1/"


def test_resolve_canonical_model_falls_back_to_pydanticai_for_other_strings() -> None:
    resolved = resolve_canonical_model("test")
    inferred = infer_model("test")

    assert type(resolved) is type(inferred)
    assert resolved.model_name == inferred.model_name


def test_resolve_canonical_model_uses_env_defaults_when_base_url_is_unset(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")

    assert isinstance(model, OpenAIResponsesModel)
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


def test_build_canonical_model_settings_enable_openai_server_history() -> None:
    model = OpenAIResponsesModel(
        "gpt-5.3-codex",
        provider=OpenAIProvider(base_url="https://example.test/v1", api_key="test-key"),
    )

    assert build_canonical_model_settings(
        model=model,
        enable_server_history=True,
    ) == {
        "openai_previous_response_id": "auto",
        "parallel_tool_calls": True,
    }


def test_build_canonical_model_settings_enable_parallel_tool_calls_for_supported_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = infer_model("anthropic:claude-3-5-haiku-latest")

    assert isinstance(model, AnthropicModel)
    assert build_canonical_model_settings(model=model) == {
        "parallel_tool_calls": True
    }


def test_build_canonical_model_settings_enable_parallel_tool_calls_for_ollama(
) -> None:
    model = OpenAIChatModel(
        "glm-5:cloud",
        provider=OllamaProvider(base_url="https://example.test/v1", api_key="test-key"),
    )

    assert build_canonical_model_settings(model=model) == {
        "parallel_tool_calls": True
    }


def test_resolve_canonical_model_uses_retrying_openai_http_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("openai-responses:gpt-5.3-codex")
    client = model._provider.client

    assert isinstance(model, OpenAIResponsesModel)
    assert client.max_retries == 0
    assert isinstance(client._client._transport, AsyncTenacityTransport)


def test_resolve_canonical_model_uses_retrying_ollama_http_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.test/v1")

    model = resolve_canonical_model("ollama:glm-5:cloud")
    client = model._provider.client

    assert isinstance(model, OpenAIChatModel)
    assert isinstance(model._provider, OllamaProvider)
    assert client.max_retries == 0
    assert isinstance(client._client._transport, AsyncTenacityTransport)


def test_resolve_canonical_model_uses_default_ollama_base_url_when_unset(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    model = resolve_canonical_model("ollama:glm-5:cloud")

    assert isinstance(model, OpenAIChatModel)
    assert isinstance(model._provider, OllamaProvider)
    assert model._provider.base_url == f"{DEFAULT_OLLAMA_BASE_URL}/"


def test_resolve_canonical_model_wraps_with_instrumentation_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE", "1")
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

    assert build_canonical_model_settings(
        model=model,
        enable_server_history=True,
    ) == {
        "openai_previous_response_id": "auto",
        "parallel_tool_calls": True,
    }


def test_get_model_context_window_tokens_for_supported_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert get_model_context_window_tokens("openai-responses:gpt-5.3-codex") == 400_000
    assert get_model_context_window_tokens("openai:gpt-4o") == 128_000
    assert get_model_context_window_tokens("ollama:glm-5:cloud") == 198_000
    assert get_model_context_window_tokens("ollama:kimi-k2:1t-cloud") == 256_000


def test_build_in_run_compaction_soft_char_limit_scales_with_model_context(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert (
        build_in_run_compaction_soft_char_limit("openai-responses:gpt-5.3-codex")
        == 1_280_000
    )
    assert build_in_run_compaction_soft_char_limit("openai:gpt-4o") == 409_600
    assert build_in_run_compaction_soft_char_limit("ollama:glm-5:cloud") == 633_600
    assert (
        build_in_run_compaction_soft_char_limit("ollama:kimi-k2:1t-cloud")
        == 819_200
    )


def test_build_in_run_compaction_soft_char_limit_uses_default_for_unknown_models(
) -> None:
    model = FunctionModel(function=lambda _messages, _info: "")

    assert get_model_context_window_tokens(model) is None
    assert build_in_run_compaction_soft_char_limit(model) == 12_000
