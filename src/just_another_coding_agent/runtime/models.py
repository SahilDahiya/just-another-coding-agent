from __future__ import annotations

import os
from typing import Any

import httpx
from anthropic import AsyncAnthropic
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
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.settings import ModelSettings
from tenacity import retry_if_exception_type, stop_after_attempt

from just_another_coding_agent.auth import (
    resolve_github_copilot_oauth_credentials_sync,
    resolve_openai_codex_oauth_credentials_sync,
    resolve_provider_secret,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.oauth_github_copilot import (
    get_github_copilot_base_url,
)
from just_another_coding_agent.provider_readiness import (
    ProviderReadinessError,
    compute_provider_readiness,
)
from just_another_coding_agent.runtime.env import trace_mode

OPENAI_COMPATIBLE_RETRYABLE_STATUS_CODES = frozenset(
    {408, 409, 429, 500, 502, 503, 504}
)
OPENAI_COMPATIBLE_HTTP_RETRY_ATTEMPTS = 3
OPENAI_COMPATIBLE_HTTP_RETRY_MAX_WAIT_SECONDS = 30
OPENAI_CODEX_MODEL_NAME_BY_ID: dict[str, str] = {
    "gpt-5-codex": "gpt-5-codex",
    "gpt-5-chatgpt": "gpt-5",
    "gpt-5-mini-chatgpt": "gpt-5-mini",
    "gpt-5.1-chatgpt": "gpt-5.1",
    "gpt-5.1-codex-chatgpt": "gpt-5.1-codex",
    "gpt-5.1-codex-mini-chatgpt": "gpt-5.1-codex-mini",
    "gpt-5.1-codex-max-chatgpt": "gpt-5.1-codex-max",
    "gpt-5.2-chatgpt": "gpt-5.2",
    "gpt-5.2-codex-chatgpt": "gpt-5.2-codex",
    "gpt-5.3-codex-chatgpt": "gpt-5.3-codex",
    "gpt-5.4-chatgpt": "gpt-5.4",
    "gpt-5.4-mini-chatgpt": "gpt-5.4-mini",
}
OPENAI_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("gpt-5.4-mini-chatgpt", 400_000),
    ("gpt-5.4-chatgpt", 400_000),
    ("gpt-5.3-codex-chatgpt", 400_000),
    ("gpt-5.2-codex-chatgpt", 400_000),
    ("gpt-5.2-chatgpt", 264_000),
    ("gpt-5.1-codex-max-chatgpt", 400_000),
    ("gpt-5.1-codex-mini-chatgpt", 400_000),
    ("gpt-5.1-codex-chatgpt", 400_000),
    ("gpt-5.1-chatgpt", 264_000),
    ("gpt-5-mini-chatgpt", 264_000),
    ("gpt-5-chatgpt", 128_000),
    ("gpt-5.4-mini-copilot", 400_000),
    ("gpt-5.4-copilot", 400_000),
    ("gpt-5.3-codex-copilot", 400_000),
    ("gpt-5.2-codex-copilot", 400_000),
    ("gpt-5.2-copilot", 264_000),
    ("gpt-5.1-codex-max-copilot", 400_000),
    ("gpt-5.1-codex-mini-copilot", 400_000),
    ("gpt-5.1-codex-copilot", 400_000),
    ("gpt-5.1-copilot", 264_000),
    ("gpt-5-mini-copilot", 264_000),
    ("gpt-5-copilot", 128_000),
    ("gpt-4.1-copilot", 128_000),
    ("gpt-4o-copilot", 128_000),
    ("gemini-2.5-pro-copilot", 128_000),
    ("gemini-3-flash-preview-copilot", 128_000),
    ("gemini-3-pro-preview-copilot", 128_000),
    ("gemini-3.1-pro-preview-copilot", 128_000),
    ("grok-code-fast-1-copilot", 128_000),
    ("gpt-5.4-mini", 400_000),
    ("gpt-5.4", 1_050_000),
    ("gpt-5-mini", 264_000),
    ("gpt-5.3-codex", 400_000),
    ("gpt-5-codex", 400_000),
    ("gpt-4o", 128_000),
)
ANTHROPIC_CONTEXT_WINDOW_TOKENS_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("claude-opus-4.6-copilot", 1_000_000),
    ("claude-sonnet-4.6-copilot", 1_000_000),
    ("claude-sonnet-4-copilot", 216_000),
    ("claude-opus-4.5-copilot", 160_000),
    ("claude-haiku-4.5-copilot", 144_000),
    ("claude-sonnet-4.5-copilot", 144_000),
    ("claude-haiku-4-5", 200_000),
    ("claude-sonnet-4-5", 200_000),
    ("claude-opus-4-1", 200_000),
)
OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
EXTERNAL_MODEL_ID_ATTR = "_jaca_external_model_id"
COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
    "X-Initiator": "user",
    "Openai-Intent": "conversation-edits",
}


def resolve_canonical_model(model: Any) -> Model:
    if isinstance(model, Model):
        return _maybe_instrument_model(model)

    if isinstance(model, str):
        if model.startswith("openai-responses:"):
            return _maybe_instrument_model(
                _tag_external_model_id(_build_openai_responses_model(model), model)
            )
        if model.startswith("openai:") or model.startswith("openai-chat:"):
            return _maybe_instrument_model(
                _tag_external_model_id(_build_openai_chat_model(model), model)
            )
        if model.startswith("anthropic:"):
            return _maybe_instrument_model(
                _tag_external_model_id(_build_anthropic_model(model), model)
            )

    return _maybe_instrument_model(infer_model(model))


def _build_openai_responses_model(model_id: str) -> OpenAIResponsesModel:
    _, model_name = model_id.split(":", 1)
    codex_model_name = _openai_codex_model_name(model_name)
    if codex_model_name is not None:
        return OpenAIResponsesModel(
            codex_model_name,
            provider=_build_openai_codex_oauth_provider(),
        )
    copilot_model_name = _github_copilot_model_name(model_name)
    if copilot_model_name is not None:
        return OpenAIResponsesModel(
            copilot_model_name,
            provider=_build_github_copilot_provider(),
        )
    return OpenAIResponsesModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    copilot_model_name = _github_copilot_model_name(model_name)
    if copilot_model_name is not None:
        return OpenAIChatModel(
            copilot_model_name,
            provider=_build_github_copilot_provider(),
        )
    return OpenAIChatModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_provider() -> OpenAIProvider:
    readiness = compute_provider_readiness("openai")
    if not readiness.configured:
        raise ProviderReadinessError("OpenAI is not ready: missing secret")
    api_key = resolve_provider_secret("openai")

    return OpenAIProvider(
        openai_client=_build_openai_compatible_client(
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=api_key,
        )
    )


def _build_openai_codex_oauth_provider() -> OpenAIProvider:
    credentials = resolve_openai_codex_oauth_credentials_sync()
    if credentials is None:
        raise ProviderReadinessError(
            "ChatGPT subscription login required for openai-responses:gpt-5-codex"
        )
    return OpenAIProvider(
        openai_client=_build_openai_compatible_client(
            base_url=OPENAI_CODEX_BASE_URL,
            api_key=credentials.access,
            default_headers={
                "chatgpt-account-id": credentials.account_id,
                "originator": "jaca",
                "OpenAI-Beta": "responses=experimental",
            },
        )
    )


def _build_github_copilot_provider() -> OpenAIProvider:
    credentials = resolve_github_copilot_oauth_credentials_sync()
    if credentials is None:
        raise ProviderReadinessError(
            "GitHub Copilot login required for Copilot-backed openai-responses models"
        )
    return OpenAIProvider(
        openai_client=_build_openai_compatible_client(
            base_url=get_github_copilot_base_url(
                credentials.access,
                credentials.enterprise_domain,
            ),
            api_key=credentials.access,
            default_headers=COPILOT_HEADERS,
        )
    )


def _build_anthropic_model(model_id: str) -> AnthropicModel:
    _, model_name = model_id.split(":", 1)
    copilot_model_name = _github_copilot_model_name(model_name)
    if copilot_model_name is not None:
        return AnthropicModel(
            copilot_model_name,
            provider=_build_github_copilot_anthropic_provider(),
        )
    return AnthropicModel(
        model_name,
        provider=_build_anthropic_provider(),
    )


def _build_anthropic_provider() -> AnthropicProvider:
    readiness = compute_provider_readiness("anthropic")
    if not readiness.configured:
        raise ProviderReadinessError("Anthropic is not ready: missing secret")
    return AnthropicProvider(api_key=resolve_provider_secret("anthropic"))


def _build_github_copilot_anthropic_provider() -> AnthropicProvider:
    credentials = resolve_github_copilot_oauth_credentials_sync()
    if credentials is None:
        raise ProviderReadinessError(
            "GitHub Copilot login required for Copilot-backed anthropic models"
        )
    return AnthropicProvider(
        anthropic_client=AsyncAnthropic(
            auth_token=credentials.access,
            base_url=get_github_copilot_base_url(
                credentials.access,
                credentials.enterprise_domain,
            ),
            default_headers=COPILOT_HEADERS,
            max_retries=0,
        )
    )


def _build_openai_compatible_client(
    *,
    base_url: str | None,
    api_key: str | None,
    default_headers: dict[str, str] | None = None,
) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers=default_headers,
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
        _apply_openai_codex_policy(settings=settings, model=policy_model)
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


def _apply_openai_codex_policy(*, settings: dict[str, Any], model: Model) -> None:
    if not isinstance(model, OpenAIResponsesModel):
        return
    if not isinstance(model._provider, OpenAIProvider):
        return
    base_url = str(model._provider.base_url)
    is_codex_backend = base_url == f"{OPENAI_CODEX_BASE_URL}/"
    is_copilot_backend = "githubcopilot.com" in base_url
    if not is_codex_backend and not is_copilot_backend:
        return
    # The ChatGPT Codex and Copilot backends reject standard Responses
    # continuation semantics; each request must be a fresh non-stored input.
    settings.pop("openai_previous_response_id", None)
    settings["openai_store"] = False


def _github_copilot_model_name(model_name: str) -> str | None:
    if not model_name.endswith("-copilot"):
        return None
    base_model = model_name[: -len("-copilot")]
    if not base_model:
        return None
    return base_model


def _openai_codex_model_name(model_name: str) -> str | None:
    return OPENAI_CODEX_MODEL_NAME_BY_ID.get(model_name)


def _supports_parallel_tool_calls(model: Model) -> bool:
    if isinstance(model, OpenAIResponsesModel):
        return isinstance(model._provider, OpenAIProvider)
    if isinstance(model, OpenAIChatModel):
        return isinstance(model._provider, OpenAIProvider)
    return isinstance(model, AnthropicModel)


def get_external_model_id(model: Any) -> str | None:
    if isinstance(model, str):
        return model

    resolved_model = resolve_canonical_model(model)
    current: Any = resolved_model
    while True:
        model_id = getattr(current, EXTERNAL_MODEL_ID_ATTR, None)
        if isinstance(model_id, str) and model_id:
            return model_id
        if not isinstance(current, WrapperModel):
            break
        current = current.wrapped
    return None


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
        return None

    resolved_model = resolve_canonical_model(model)
    policy_model = _unwrap_policy_model(resolved_model)
    external_model_id = get_external_model_id(policy_model)
    if external_model_id:
        return get_model_context_window_tokens(external_model_id)

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

    if isinstance(policy_model, AnthropicModel):
        return _match_model_name_prefix(
            policy_model.model_name,
            ANTHROPIC_CONTEXT_WINDOW_TOKENS_BY_PREFIX,
        )

    return None

def _match_model_name_prefix(
    model_name: str,
    candidates: tuple[tuple[str, int], ...],
) -> int | None:
    for prefix, context_window_tokens in candidates:
        if model_name.startswith(prefix):
            return context_window_tokens

    return None


def _tag_external_model_id(model: Model, model_id: str) -> Model:
    setattr(model, EXTERNAL_MODEL_ID_ATTR, model_id)
    return model


__all__ = [
    "build_canonical_model_settings",
    "get_external_model_id",
    "get_model_context_window_tokens",
    "resolve_canonical_model",
]
