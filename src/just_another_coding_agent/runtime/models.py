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
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.github import GitHubProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.settings import ModelSettings
from tenacity import retry_if_exception_type, stop_after_attempt

from just_another_coding_agent.auth import resolve_provider_secret
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.env import trace_mode

OPENAI_COMPATIBLE_RETRYABLE_STATUS_CODES = frozenset(
    {408, 409, 429, 500, 502, 503, 504}
)
OPENAI_COMPATIBLE_HTTP_RETRY_ATTEMPTS = 3
OPENAI_COMPATIBLE_HTTP_RETRY_MAX_WAIT_SECONDS = 30
DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT = 12_000
IN_RUN_COMPACTION_CONTEXT_WINDOW_UTILIZATION = 0.8
IN_RUN_COMPACTION_CHARS_PER_TOKEN_HEURISTIC = 4
OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("gpt-5.4-mini", 400_000),
    ("gpt-5.4", 1_050_000),
    ("gpt-5.3-codex", 400_000),
    ("gpt-5-codex", 400_000),
    ("gpt-4o", 128_000),
)
ANTHROPIC_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("claude-sonnet-4-5", 200_000),
    ("claude-opus-4-1", 200_000),
)
GITHUB_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("openai/gpt-5", 200_000),
    ("openai/gpt-5-mini", 200_000),
    ("openai/gpt-4.1", 1_048_576),
)
OLLAMA_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("qwen3.5", 262_144),
    ("qwen3-coder-next", 262_144),
    ("glm-5", 198_000),
    ("kimi-k2", 262_144),
)
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"


def resolve_canonical_model(model: Any) -> Model:
    if isinstance(model, Model):
        return _maybe_instrument_model(model)

    if isinstance(model, str):
        if model.startswith("openai-responses:"):
            return _maybe_instrument_model(_build_openai_responses_model(model))
        if model.startswith("openai:") or model.startswith("openai-chat:"):
            return _maybe_instrument_model(_build_openai_chat_model(model))
        if model.startswith("anthropic:"):
            return _maybe_instrument_model(_build_anthropic_model(model))
        if model.startswith("github:"):
            return _maybe_instrument_model(_build_github_chat_model(model))
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
    api_key = resolve_provider_secret("openai")
    if api_key is None and "OPENAI_API_KEY" not in os.environ and base_url is not None:
        api_key = "api-key-not-set"

    return OpenAIProvider(
        openai_client=_build_openai_compatible_client(
            base_url=base_url,
            api_key=api_key,
        )
    )


def _build_anthropic_model(model_id: str) -> AnthropicModel:
    _, model_name = model_id.split(":", 1)
    return AnthropicModel(
        model_name,
        provider=_build_anthropic_provider(),
    )


def _build_anthropic_provider() -> AnthropicProvider:
    return AnthropicProvider(api_key=resolve_provider_secret("anthropic"))


def _build_ollama_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIChatModel(
        model_name,
        provider=_build_ollama_provider(),
    )


def _build_ollama_provider() -> OllamaProvider:
    base_url = os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
    api_key = (
        resolve_provider_secret("ollama", allow_missing_keychain=True)
        or "api-key-not-set"
    )
    return OllamaProvider(
        openai_client=_build_openai_compatible_client(
            base_url=base_url,
            api_key=api_key,
        )
    )


def _build_github_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIChatModel(
        model_name,
        provider=_build_github_provider(),
    )


def _build_github_provider() -> GitHubProvider:
    api_key = resolve_provider_secret("github")
    return GitHubProvider(
        openai_client=_build_openai_compatible_client(
            base_url="https://models.github.ai/inference",
            api_key=api_key,
        ),
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
) -> ModelSettings | None:
    settings: dict[str, Any] = {}
    if model is not None:
        resolved_model = resolve_canonical_model(model)
        policy_model = _unwrap_policy_model(resolved_model)
        settings.update(resolved_model.settings or {})
        _apply_parallel_tool_call_policy(settings=settings, model=policy_model)
    if thinking is not None:
        settings["thinking"] = thinking

    return settings or None


def _maybe_instrument_model(model: Model) -> Model:
    if trace_mode() == "off":
        return model
    if isinstance(model, InstrumentedModel):
        return model
    return InstrumentedModel(model)


def _unwrap_policy_model(model: Model) -> Model:
    current = model
    while isinstance(current, WrapperModel):
        current = current.wrapped
    return current


def unwrap_instrumented_model(model: Model) -> Model:
    """Unwrap instrumentation wrappers to get the underlying model.

    Only unwraps InstrumentedModel wrappers, preserving other policy wrappers.
    Useful for testing to assert on the actual model type.
    """
    current = model
    while isinstance(current, InstrumentedModel):
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
        return isinstance(
            model._provider,
            (OpenAIProvider, OllamaProvider, GitHubProvider),
        )
    return isinstance(model, AnthropicModel)


def get_model_context_window_tokens(model: Any) -> int | None:
    if isinstance(model, str):
        if model.startswith("openai-responses:"):
            return _match_model_name_prefix(
                model.split(":", 1)[1],
                OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if model.startswith("openai:") or model.startswith("openai-chat:"):
            return _match_model_name_prefix(
                model.split(":", 1)[1],
                OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if model.startswith("anthropic:"):
            return _match_model_name_prefix(
                model.split(":", 1)[1],
                ANTHROPIC_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if model.startswith("github:"):
            return _match_model_name_prefix(
                model.split(":", 1)[1],
                GITHUB_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if model.startswith("ollama:"):
            return _match_model_name_prefix(
                model.split(":", 1)[1],
                OLLAMA_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        return None

    resolved_model = resolve_canonical_model(model)
    policy_model = _unwrap_policy_model(resolved_model)

    if isinstance(policy_model, OpenAIResponsesModel):
        return _match_model_name_prefix(
            policy_model.model_name,
            OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
        )

    if isinstance(policy_model, OpenAIChatModel):
        if isinstance(policy_model._provider, OpenAIProvider):
            return _match_model_name_prefix(
                policy_model.model_name,
                OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if isinstance(policy_model._provider, GitHubProvider):
            return _match_model_name_prefix(
                policy_model.model_name,
                GITHUB_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )
        if isinstance(policy_model._provider, OllamaProvider):
            return _match_model_name_prefix(
                policy_model.model_name,
                OLLAMA_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
            )

    if isinstance(policy_model, AnthropicModel):
        return _match_model_name_prefix(
            policy_model.model_name,
            ANTHROPIC_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
        )

    return None


def build_in_run_compaction_soft_char_limit(model: Any) -> int:
    context_window_tokens = get_model_context_window_tokens(model)
    if context_window_tokens is None:
        return DEFAULT_IN_RUN_COMPACTION_SOFT_CHAR_LIMIT

    return int(
        context_window_tokens
        * IN_RUN_COMPACTION_CONTEXT_WINDOW_UTILIZATION
        * IN_RUN_COMPACTION_CHARS_PER_TOKEN_HEURISTIC
    )


def _match_model_name_prefix(
    model_name: str,
    candidates: tuple[tuple[str, int], ...],
) -> int | None:
    for prefix, context_window_tokens in candidates:
        if model_name.startswith(prefix):
            return context_window_tokens

    return None


__all__ = [
    "build_canonical_model_settings",
    "build_in_run_compaction_soft_char_limit",
    "get_model_context_window_tokens",
    "resolve_canonical_model",
]
