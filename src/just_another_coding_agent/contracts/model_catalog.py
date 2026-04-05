from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ProviderName = Literal["openai", "openrouter", "anthropic", "ollama", "google"]


class _ModelCatalogBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShippedModel(_ModelCatalogBase):
    provider: ProviderName
    model_id: str
    description: str
    default_for_provider: bool = False


CANONICAL_PROVIDER_ORDER: tuple[ProviderName, ...] = (
    "ollama",
    "openai",
    "openrouter",
    "anthropic",
    "google",
)

SHIPPED_MODELS: tuple[ShippedModel, ...] = (
    ShippedModel(
        provider="ollama",
        model_id="ollama:kimi-k2:1t-cloud",
        description="Current default Kimi K2",
        default_for_provider=True,
    ),
    ShippedModel(
        provider="ollama",
        model_id="ollama:glm-5:cloud",
        description="GLM-5 cloud path",
    ),
    ShippedModel(
        provider="ollama",
        model_id="ollama:qwen3.5:397b-cloud",
        description="Qwen 3.5 397B cloud",
    ),
    ShippedModel(
        provider="ollama",
        model_id="ollama:qwen3-coder-next",
        description="Qwen3 Coder Next",
    ),
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
        provider="openrouter",
        model_id="openrouter:anthropic/claude-sonnet-4-5",
        description="OpenRouter Claude Sonnet",
        default_for_provider=True,
    ),
    ShippedModel(
        provider="google",
        model_id="google:gemini-2.5-flash",
        description="Fast Gemini 2.5 Flash",
        default_for_provider=True,
    ),
    ShippedModel(
        provider="google",
        model_id="google:gemini-2.5-flash-lite",
        description="Cheaper Gemini 2.5 Flash-Lite",
    ),
    ShippedModel(
        provider="google",
        model_id="google:gemini-2.5-pro",
        description="Stronger Gemini 2.5 Pro",
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
