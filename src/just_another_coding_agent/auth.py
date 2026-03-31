from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

SECRET_STORE_SERVICE = "just-another-coding-agent"
ProviderName = Literal["ollama", "openai", "anthropic", "github"]
AuthSource = Literal["env", "keychain", "none"]

PROVIDER_SECRET_ENV_KEYS: dict[ProviderName, str] = {
    "ollama": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "github": "GITHUB_API_KEY",
}


class ProviderAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    configured: bool
    source: AuthSource


def resolve_provider_secret(
    provider: ProviderName,
    *,
    allow_missing_keychain: bool = False,
) -> str | None:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value
    return _get_keychain_secret(
        provider,
        allow_missing_keychain=allow_missing_keychain,
    )


def get_provider_auth_status(provider: ProviderName) -> ProviderAuthStatus:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return ProviderAuthStatus(
            provider=provider,
            configured=True,
            source="env",
        )

    keychain_value = _get_keychain_secret(provider, allow_missing_keychain=True)
    if keychain_value:
        return ProviderAuthStatus(
            provider=provider,
            configured=True,
            source="keychain",
        )

    return ProviderAuthStatus(
        provider=provider,
        configured=False,
        source="none",
    )


def list_provider_auth_statuses() -> list[ProviderAuthStatus]:
    return [get_provider_auth_status(provider) for provider in PROVIDER_SECRET_ENV_KEYS]


def set_provider_secret(provider: ProviderName, secret: str) -> ProviderAuthStatus:
    trimmed = secret.strip()
    if not trimmed:
        raise ValueError("provider secret must be a non-empty string")

    keyring = _load_keyring()
    env_key = _provider_env_key(provider)
    keyring.set_password(SECRET_STORE_SERVICE, env_key, trimmed)
    return get_provider_auth_status(provider)


def clear_provider_secret(provider: ProviderName) -> ProviderAuthStatus:
    keyring = _load_keyring()
    env_key = _provider_env_key(provider)
    try:
        keyring.delete_password(SECRET_STORE_SERVICE, env_key)
    except keyring.errors.PasswordDeleteError:
        pass
    return get_provider_auth_status(provider)


def _provider_env_key(provider: ProviderName) -> str:
    try:
        return PROVIDER_SECRET_ENV_KEYS[provider]
    except KeyError as error:  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown provider: {provider}") from error


def _get_keychain_secret(
    provider: ProviderName,
    *,
    allow_missing_keychain: bool = False,
) -> str | None:
    try:
        keyring = _load_keyring()
    except RuntimeError:
        if allow_missing_keychain:
            return None
        raise
    env_key = _provider_env_key(provider)
    try:
        value = keyring.get_password(SECRET_STORE_SERVICE, env_key)
    except keyring.errors.KeyringError:
        if allow_missing_keychain:
            return None
        raise
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _load_keyring():
    try:
        import keyring
    except ImportError as error:  # pragma: no cover - dependency contract
        raise RuntimeError(
            "Python dependency 'keyring' is required for local provider secret storage"
        ) from error

    return keyring


__all__ = [
    "AuthSource",
    "PROVIDER_SECRET_ENV_KEYS",
    "ProviderAuthStatus",
    "ProviderName",
    "clear_provider_secret",
    "get_provider_auth_status",
    "list_provider_auth_statuses",
    "resolve_provider_secret",
    "set_provider_secret",
]
