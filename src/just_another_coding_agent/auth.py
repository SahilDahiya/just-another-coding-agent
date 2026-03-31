from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

from just_another_coding_agent.contracts.model_catalog import ProviderName

SECRET_STORE_SERVICE = "just-another-coding-agent"
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


class AuthStoreError(RuntimeError):
    pass


class ProviderSecretValidationError(ValueError):
    pass


def _missing_keyring_backend_message() -> str:
    return (
        "No supported OS keychain backend is available for local provider secret "
        "storage. Configure a supported system keychain and try again. On "
        "Linux/WSL, install and unlock a Secret Service backend such as "
        "gnome-keyring."
    )


def _auth_store_error_message(*, keyring, error: Exception) -> str:
    no_keyring_error = getattr(keyring.errors, "NoKeyringError", None)
    if no_keyring_error is not None and isinstance(error, no_keyring_error):
        return _missing_keyring_backend_message()
    if "No recommended backend was available" in str(error):
        return _missing_keyring_backend_message()
    return str(error)


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
        raise ProviderSecretValidationError(
            "provider secret must be a non-empty string"
        )

    keyring = _load_keyring()
    env_key = _provider_env_key(provider)
    try:
        keyring.set_password(SECRET_STORE_SERVICE, env_key, trimmed)
    except keyring.errors.KeyringError as error:
        raise AuthStoreError(
            _auth_store_error_message(keyring=keyring, error=error)
        ) from error
    return get_provider_auth_status(provider)


def clear_provider_secret(provider: ProviderName) -> ProviderAuthStatus:
    keyring = _load_keyring()
    env_key = _provider_env_key(provider)
    try:
        keyring.delete_password(SECRET_STORE_SERVICE, env_key)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as error:
        raise AuthStoreError(
            _auth_store_error_message(keyring=keyring, error=error)
        ) from error
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
    except AuthStoreError:
        if allow_missing_keychain:
            return None
        raise
    env_key = _provider_env_key(provider)
    try:
        value = keyring.get_password(SECRET_STORE_SERVICE, env_key)
    except keyring.errors.KeyringError as error:
        if allow_missing_keychain:
            return None
        raise AuthStoreError(
            _auth_store_error_message(keyring=keyring, error=error)
        ) from error
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _load_keyring():
    try:
        import keyring
    except ImportError as error:  # pragma: no cover - dependency contract
        raise AuthStoreError(
            "Python dependency 'keyring' is required for local provider secret storage"
        ) from error

    return keyring


__all__ = [
    "AuthStoreError",
    "AuthSource",
    "PROVIDER_SECRET_ENV_KEYS",
    "ProviderAuthStatus",
    "ProviderName",
    "ProviderSecretValidationError",
    "clear_provider_secret",
    "get_provider_auth_status",
    "list_provider_auth_statuses",
    "resolve_provider_secret",
    "set_provider_secret",
]
