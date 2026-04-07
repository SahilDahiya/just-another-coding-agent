from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ProviderName = Literal["openai", "anthropic"]


class _ModelCatalogBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShippedModel(_ModelCatalogBase):
    provider: ProviderName
    model_id: str
    description: str
    default_for_provider: bool = False


CANONICAL_PROVIDER_ORDER: tuple[ProviderName, ...] = (
    "openai",
    "anthropic",
)

SHIPPED_MODELS: tuple[ShippedModel, ...] = (
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4",
        description="Default GPT-5.4 Responses path",
        default_for_provider=True,
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-mini",
        description="Faster GPT-5.4 mini Responses path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.3-codex",
        description="Codex-optimized GPT-5.3 Responses path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5-codex",
        description="Experimental ChatGPT subscription Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5-chatgpt",
        description="Experimental ChatGPT subscription GPT-5 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5-mini-chatgpt",
        description="Experimental ChatGPT subscription GPT-5 mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.1 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.1 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-mini-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.1 Codex Mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-max-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.1 Codex Max path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.2-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.2 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.2-codex-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.2 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.3-codex-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.3 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.4 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-mini-chatgpt",
        description="Experimental ChatGPT subscription GPT-5.4 mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5-copilot",
        description="Experimental GitHub Copilot GPT-5 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5-mini-copilot",
        description="Experimental GitHub Copilot GPT-5 mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-copilot",
        description="Experimental GitHub Copilot GPT-5.1 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-copilot",
        description="Experimental GitHub Copilot GPT-5.1 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-mini-copilot",
        description="Experimental GitHub Copilot GPT-5.1 Codex Mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.1-codex-max-copilot",
        description="Experimental GitHub Copilot GPT-5.1 Codex Max path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.2-copilot",
        description="Experimental GitHub Copilot GPT-5.2 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.2-codex-copilot",
        description="Experimental GitHub Copilot GPT-5.2 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.3-codex-copilot",
        description="Experimental GitHub Copilot GPT-5.3 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-copilot",
        description="Experimental GitHub Copilot GPT-5.4 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-mini-copilot",
        description="Experimental GitHub Copilot GPT-5.4 mini path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gpt-4.1-copilot",
        description="Experimental GitHub Copilot GPT-4.1 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gpt-4o-copilot",
        description="Experimental GitHub Copilot GPT-4o path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gemini-2.5-pro-copilot",
        description="Experimental GitHub Copilot Gemini 2.5 Pro path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gemini-3-flash-preview-copilot",
        description="Experimental GitHub Copilot Gemini 3 Flash path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gemini-3-pro-preview-copilot",
        description="Experimental GitHub Copilot Gemini 3 Pro path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:gemini-3.1-pro-preview-copilot",
        description="Experimental GitHub Copilot Gemini 3.1 Pro path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-chat:grok-code-fast-1-copilot",
        description="Experimental GitHub Copilot Grok Code Fast 1 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-sonnet-4-5",
        description="Balanced Claude Sonnet",
        default_for_provider=True,
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-opus-4-1",
        description="Stronger Claude Opus",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-haiku-4.5-copilot",
        description="Experimental GitHub Copilot Claude Haiku 4.5 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-opus-4.5-copilot",
        description="Experimental GitHub Copilot Claude Opus 4.5 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-opus-4.6-copilot",
        description="Experimental GitHub Copilot Claude Opus 4.6 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-sonnet-4-copilot",
        description="Experimental GitHub Copilot Claude Sonnet 4 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-sonnet-4.5-copilot",
        description="Experimental GitHub Copilot Claude Sonnet 4.5 path",
    ),
    ShippedModel(
        provider="anthropic",
        model_id="anthropic:claude-sonnet-4.6-copilot",
        description="Experimental GitHub Copilot Claude Sonnet 4.6 path",
    ),
)


def shipped_models() -> tuple[ShippedModel, ...]:
    _validate_context_windows()
    return SHIPPED_MODELS


def shipped_models_for_provider(provider: ProviderName) -> tuple[ShippedModel, ...]:
    return tuple(model for model in shipped_models() if model.provider == provider)


def default_model_for_provider(provider: ProviderName) -> str:
    for model in shipped_models_for_provider(provider):
        if model.default_for_provider:
            return model.model_id
    raise RuntimeError(f"missing default shipped model for provider: {provider}")


def _validate_context_windows() -> None:
    from just_another_coding_agent.runtime.models import get_model_context_window_tokens

    missing = [
        model.model_id
        for model in SHIPPED_MODELS
        if get_model_context_window_tokens(model.model_id) is None
    ]
    if missing:
        raise RuntimeError(
            "Shipped model catalog contains models without context-window metadata: "
            + ", ".join(missing)
        )


__all__ = [
    "CANONICAL_PROVIDER_ORDER",
    "ProviderName",
    "ShippedModel",
    "default_model_for_provider",
    "shipped_models",
    "shipped_models_for_provider",
]
