from __future__ import annotations

import os
from dataclasses import dataclass

from just_another_coding_agent import secret_store
from just_another_coding_agent.contracts.auth import (
    AuthSource,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import (
    ProviderName,
    is_removed_openai_model_id,
)
from just_another_coding_agent.oauth_store import (
    get_openai_codex_credentials,
)

_OPENAI_CODEX_OAUTH_ENV_KEYS = (
    "OPENAI_CODEX_OAUTH_ACCESS_TOKEN",
    "OPENAI_CODEX_OAUTH_REFRESH_TOKEN",
    "OPENAI_CODEX_OAUTH_EXPIRES_AT",
    "OPENAI_CODEX_OAUTH_ACCOUNT_ID",
)


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
    if is_removed_openai_model_id(model_id):
        raise ValueError(f"unsupported model id: {model_id}")
    provider = _provider_for_model(model_id)
    if provider is None:
        raise ValueError(f"unsupported model id: {model_id}")
    if _is_openai_codex_model(model_id):
        credentials_ready = _has_openai_codex_credentials()
        return ProviderAuthStatus(
            provider=provider,
            configured=credentials_ready,
            secret_configured=False,
            requires_secret=False,
            source="none",
            env_key=_provider_env_key(provider),
            reason="ok" if credentials_ready else "missing_secret",
        )
    return compute_provider_readiness(provider, model_id=model_id)


def get_provider_secret_state(provider: ProviderName) -> ProviderSecretState:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return ProviderSecretState(configured=True, source="env")

    file_store_value = secret_store.get_file_store_secret(provider)
    if file_store_value:
        return ProviderSecretState(configured=True, source="file")

    return ProviderSecretState(configured=False, source="none")


def _provider_requires_secret(provider: ProviderName, *, model_id: str | None) -> bool:
    del model_id
    return provider in {"openai", "anthropic"}


def _provider_env_key(provider: ProviderName) -> str:
    return secret_store.provider_env_key(provider)


def _provider_for_model(model_id: str) -> ProviderName | None:
    if model_id.startswith(("openai:", "openai-chat:", "openai-responses:")):
        return "openai"
    if model_id.startswith("anthropic:"):
        return "anthropic"
    return None

def _is_openai_codex_model(model_id: str) -> bool:
    return model_id == "openai-responses:gpt-5-codex" or (
        model_id.startswith("openai-responses:") and model_id.endswith("-chatgpt")
    )


def _has_openai_codex_credentials() -> bool:
    if get_openai_codex_credentials() is not None:
        return True
    return all(os.environ.get(key, "").strip() for key in _OPENAI_CODEX_OAUTH_ENV_KEYS)


__all__ = [
    "ProviderSecretState",
    "compute_model_readiness",
    "compute_provider_readiness",
    "get_provider_secret_state",
    "ProviderReadinessError",
]
