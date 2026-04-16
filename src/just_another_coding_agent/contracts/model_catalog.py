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

REMOVED_OPENAI_MODEL_NAMES = frozenset(
    {
        "gpt-5-codex",
        "gpt-5-chatgpt",
        "gpt-5-mini-chatgpt",
        "gpt-5.1-chatgpt",
        "gpt-5.1-codex-chatgpt",
        "gpt-5.1-codex-mini-chatgpt",
        "gpt-5.1-codex-max-chatgpt",
        "gpt-5.2-codex-chatgpt",
    }
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
        model_id="openai-responses:gpt-5.2-chatgpt",
        description="OAuth GPT-5.2 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.3-codex-chatgpt",
        description="OAuth GPT-5.3 Codex path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-chatgpt",
        description="OAuth GPT-5.4 path",
    ),
    ShippedModel(
        provider="openai",
        model_id="openai-responses:gpt-5.4-mini-chatgpt",
        description="OAuth GPT-5.4 mini path",
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


def is_removed_openai_model_name(model_name: str) -> bool:
    return model_name in REMOVED_OPENAI_MODEL_NAMES


def is_removed_openai_model_id(model_id: str) -> bool:
    if not model_id.startswith(("openai:", "openai-chat:", "openai-responses:")):
        return False
    return is_removed_openai_model_name(model_id.split(":", 1)[1])


__all__ = [
    "CANONICAL_PROVIDER_ORDER",
    "ProviderName",
    "ShippedModel",
    "default_model_for_provider",
    "shipped_models",
    "shipped_models_for_provider",
]
