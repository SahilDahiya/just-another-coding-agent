from __future__ import annotations

import os
from typing import Any

import httpx
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.settings import ModelSettings
from tenacity import retry_if_exception_type, stop_after_attempt

from just_another_coding_agent.contracts.thinking import ThinkingSetting

OPENAI_COMPATIBLE_RETRYABLE_STATUS_CODES = frozenset(
    {408, 409, 429, 500, 502, 503, 504}
)
OPENAI_COMPATIBLE_HTTP_RETRY_ATTEMPTS = 3
OPENAI_COMPATIBLE_HTTP_RETRY_MAX_WAIT_SECONDS = 30


def resolve_canonical_model(model: Any) -> Model:
    if isinstance(model, Model):
        return _maybe_instrument_model(model)

    if isinstance(model, str):
        if model.startswith("openai-responses:"):
            return _maybe_instrument_model(_build_openai_responses_model(model))
        if model.startswith("openai:") or model.startswith("openai-chat:"):
            return _maybe_instrument_model(_build_openai_chat_model(model))
        if model.startswith("ollama:"):
            return _maybe_instrument_model(_build_ollama_chat_model(model))

    return _maybe_instrument_model(infer_model(model))


def _build_openai_responses_model(model_id: str) -> OpenAIResponsesModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIResponsesModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIChatModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_provider() -> OpenAIProvider:
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key is None and "OPENAI_API_KEY" not in os.environ and base_url is not None:
        api_key = "api-key-not-set"

    return OpenAIProvider(
        openai_client=_build_openai_compatible_client(
            base_url=base_url,
            api_key=api_key,
        )
    )


def _build_ollama_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIChatModel(
        model_name,
        provider=_build_ollama_provider(),
    )


def _build_ollama_provider() -> OllamaProvider:
    base_url = os.environ.get("OLLAMA_BASE_URL")
    if base_url is None:
        return OllamaProvider()

    api_key = os.environ.get("OLLAMA_API_KEY") or "api-key-not-set"
    return OllamaProvider(
        openai_client=_build_openai_compatible_client(
            base_url=base_url,
            api_key=api_key,
        )
    )


def _build_openai_compatible_client(
    *,
    base_url: str | None,
    api_key: str | None,
) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=_build_retrying_openai_compatible_http_client(),
        max_retries=0,
    )


def _build_retrying_openai_compatible_http_client() -> DefaultAsyncHttpxClient:
    transport = AsyncTenacityTransport(
        RetryConfig(
            retry=retry_if_exception_type(
                (httpx.TransportError, httpx.HTTPStatusError)
            ),
            wait=wait_retry_after(
                max_wait=OPENAI_COMPATIBLE_HTTP_RETRY_MAX_WAIT_SECONDS
            ),
            stop=stop_after_attempt(OPENAI_COMPATIBLE_HTTP_RETRY_ATTEMPTS),
            reraise=True,
        ),
        validate_response=_raise_for_retryable_openai_status,
    )
    return DefaultAsyncHttpxClient(transport=transport)


def _raise_for_retryable_openai_status(response: httpx.Response) -> None:
    if response.status_code in OPENAI_COMPATIBLE_RETRYABLE_STATUS_CODES:
        response.raise_for_status()


def build_canonical_model_settings(
    *,
    model: Any = None,
    thinking: ThinkingSetting | None = None,
    enable_server_history: bool = False,
) -> ModelSettings | None:
    settings: dict[str, Any] = {}
    if model is not None:
        resolved_model = resolve_canonical_model(model)
        policy_model = _unwrap_policy_model(resolved_model)
        settings.update(resolved_model.settings or {})
        if (
            enable_server_history
            and isinstance(policy_model, OpenAIResponsesModel)
            and "openai_previous_response_id" not in settings
        ):
            settings.update(
                OpenAIResponsesModelSettings(openai_previous_response_id="auto")
            )
        _apply_parallel_tool_call_policy(settings=settings, model=policy_model)
    if thinking is not None:
        settings["thinking"] = thinking

    return settings or None


def _maybe_instrument_model(model: Model) -> Model:
    if not _env_flag("JACA_TRACE"):
        return model
    if isinstance(model, InstrumentedModel):
        return model
    return InstrumentedModel(model)


def _unwrap_policy_model(model: Model) -> Model:
    current = model
    while isinstance(current, WrapperModel):
        current = current.wrapped
    return current


def _apply_parallel_tool_call_policy(*, settings: dict[str, Any], model: Model) -> None:
    supported = _supports_parallel_tool_calls(model)
    configured = settings.get("parallel_tool_calls")
    if configured is not None:
        if not isinstance(configured, bool):
            raise TypeError("parallel_tool_calls must be a boolean when provided")
        if configured is not supported:
            raise ValueError(
                "parallel_tool_calls conflicts with canonical provider support"
            )
        return

    if supported:
        settings["parallel_tool_calls"] = True


def _supports_parallel_tool_calls(model: Model) -> bool:
    if isinstance(model, OpenAIResponsesModel):
        return isinstance(model._provider, OpenAIProvider)
    if isinstance(model, OpenAIChatModel):
        return isinstance(model._provider, (OpenAIProvider, OllamaProvider))
    return isinstance(model, AnthropicModel)


def _env_flag(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


__all__ = ["build_canonical_model_settings", "resolve_canonical_model"]
