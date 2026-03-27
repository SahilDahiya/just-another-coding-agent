import os

from pydantic_ai.models import infer_model
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport

from just_another_coding_agent.runtime.models import (
    build_canonical_model_settings,
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
        "thinking": "high",
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
