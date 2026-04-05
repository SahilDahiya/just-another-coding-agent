from __future__ import annotations

import os
from dataclasses import dataclass

from just_another_coding_agent.contracts.auth import (
    AuthSource,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import (
    ProviderName,
    shipped_models_for_provider,
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"

LOCALHOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class ProviderSecretState:
    configured: bool
    source: AuthSource


class ProviderReadinessError(RuntimeError):
    pass


def compute_provider_readiness(
    provider: ProviderName,
    *,
    model_id: str | None = None,
) -> ProviderAuthStatus:
    env_key = _provider_env_key(provider)
    secret_state = get_provider_secret_state(provider)
    requires_secret = _provider_requires_secret(provider, model_id=model_id)
    if requires_secret:
        return ProviderAuthStatus(
            provider=provider,
            configured=secret_state.configured,
            secret_configured=secret_state.configured,
            requires_secret=True,
            source=secret_state.source,
            env_key=env_key,
            reason="ok" if secret_state.configured else "missing_secret",
        )

    return ProviderAuthStatus(
        provider=provider,
        configured=True,
        secret_configured=secret_state.configured,
        requires_secret=False,
        source=secret_state.source,
        env_key=env_key,
        reason="local_endpoint_no_secret_required",
    )


def compute_model_readiness(model_id: str) -> ProviderAuthStatus:
    provider = _provider_for_model(model_id)
    if provider is None:
        raise ValueError(f"unsupported model id: {model_id}")
    return compute_provider_readiness(provider, model_id=model_id)


def get_provider_secret_state(provider: ProviderName) -> ProviderSecretState:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return ProviderSecretState(configured=True, source="env")

    from just_another_coding_agent.auth import (
        _get_file_store_secret,
        _get_keychain_secret,
    )

    keychain_value = _get_keychain_secret(provider, allow_missing_keychain=True)
    if keychain_value:
        return ProviderSecretState(configured=True, source="keychain")

    file_store_value = _get_file_store_secret(provider)
    if file_store_value:
        return ProviderSecretState(configured=True, source="file")

    return ProviderSecretState(configured=False, source="none")


def _provider_requires_secret(provider: ProviderName, *, model_id: str | None) -> bool:
    if provider in {"anthropic", "google", "openrouter"}:
        return True
    if provider == "openai":
        return not _base_url_is_local(os.environ.get("OPENAI_BASE_URL"))
    if provider == "ollama":
        if model_id is not None:
            return _ollama_model_requires_secret(model_id)
        return not _base_url_is_local(
            os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        )
    return True


def _ollama_model_requires_secret(model_id: str) -> bool:
    if not model_id.startswith("ollama:"):
        raise ValueError(f"ollama model_id must start with 'ollama:': {model_id}")
    hosted_ids = {model.model_id for model in shipped_models_for_provider("ollama")}
    return model_id in hosted_ids


def _base_url_is_local(base_url: str | None) -> bool:
    if base_url is None:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").strip().lower()
    return hostname in LOCALHOST_NAMES


def _provider_env_key(provider: ProviderName) -> str:
    env_keys: dict[ProviderName, str] = {
        "ollama": "OLLAMA_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    return env_keys[provider]


def _provider_for_model(model_id: str) -> ProviderName | None:
    if model_id.startswith(("openai:", "openai-chat:", "openai-responses:")):
        return "openai"
    if model_id.startswith("openrouter:"):
        return "openrouter"
    if model_id.startswith("anthropic:"):
        return "anthropic"
    if model_id.startswith("google:"):
        return "google"
    if model_id.startswith("ollama:"):
        return "ollama"
    return None


__all__ = [
    "ProviderSecretState",
    "compute_model_readiness",
    "compute_provider_readiness",
    "get_provider_secret_state",
    "ProviderReadinessError",
]
