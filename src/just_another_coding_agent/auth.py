from __future__ import annotations

import os

from just_another_coding_agent import secret_store
from just_another_coding_agent.contracts.auth import (
    AuthStorageKind,
    LocalSecretStoreStatus,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import ProviderName
from just_another_coding_agent.provider_readiness import (
    compute_provider_readiness,
)

AuthStoreError = secret_store.AuthStoreError
PROVIDER_SECRET_ENV_KEYS = secret_store.PROVIDER_SECRET_ENV_KEYS
SECRET_FILE_PATH = secret_store.SECRET_FILE_PATH
SECRET_STORE_SERVICE = secret_store.SECRET_STORE_SERVICE


class ProviderSecretValidationError(ValueError):
    pass


def _missing_keyring_backend_message() -> str:
    return secret_store.missing_keyring_backend_message()


def _auth_store_error_message(*, keyring, error: Exception) -> str:
    return secret_store.auth_store_error_message(keyring=keyring, error=error)


def resolve_provider_secret(
    provider: ProviderName,
    *,
    allow_missing_keychain: bool = False,
) -> str | None:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value
    keychain_error: AuthStoreError | None = None
    try:
        keychain_value = secret_store.get_keychain_secret(
            provider,
            allow_missing_keychain=allow_missing_keychain,
        )
    except AuthStoreError as error:
        keychain_error = error
        keychain_value = None
    if keychain_value:
        return keychain_value
    file_store_value = secret_store.get_file_store_secret(provider)
    if file_store_value:
        return file_store_value
    if keychain_error is not None and not allow_missing_keychain:
        raise keychain_error
    return None


def get_provider_auth_status(provider: ProviderName) -> ProviderAuthStatus:
    return compute_provider_readiness(provider)


def list_provider_auth_statuses() -> list[ProviderAuthStatus]:
    return [get_provider_auth_status(provider) for provider in PROVIDER_SECRET_ENV_KEYS]


def get_local_secret_store_status() -> LocalSecretStoreStatus:
    try:
        keyring = secret_store.load_keyring()
    except AuthStoreError as error:
        return LocalSecretStoreStatus(available=False, message=str(error))

    backend = keyring.get_keyring()
    priority = getattr(backend, "priority", 0)
    if priority <= 0:
        return LocalSecretStoreStatus(
            available=False,
            message=_missing_keyring_backend_message(),
            file_store_path=str(secret_store.SECRET_FILE_PATH),
        )
    return LocalSecretStoreStatus(
        available=True,
        file_store_path=str(secret_store.SECRET_FILE_PATH),
    )

def set_provider_secret(
    provider: ProviderName,
    secret: str,
    *,
    storage: AuthStorageKind = "keychain",
) -> ProviderAuthStatus:
    trimmed = secret.strip()
    if not trimmed:
        raise ProviderSecretValidationError(
            "provider secret must be a non-empty string"
        )

    env_key = _provider_env_key(provider)
    if storage == "keychain":
        keyring = secret_store.load_keyring()
        try:
            keyring.set_password(SECRET_STORE_SERVICE, env_key, trimmed)
        except keyring.errors.KeyringError as error:
            raise AuthStoreError(
                _auth_store_error_message(keyring=keyring, error=error)
            ) from error
    elif storage == "file":
        secret_store.set_file_store_secret(provider, trimmed)
    else:  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown auth storage: {storage}")
    return compute_provider_readiness(provider)


def clear_provider_secret(provider: ProviderName) -> ProviderAuthStatus:
    keyring = None
    try:
        keyring = secret_store.load_keyring()
    except AuthStoreError:
        keyring = None

    if keyring is not None:
        env_key = _provider_env_key(provider)
        try:
            keyring.delete_password(SECRET_STORE_SERVICE, env_key)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as error:
            no_keyring_error = getattr(keyring.errors, "NoKeyringError", None)
            if no_keyring_error is None or not isinstance(error, no_keyring_error):
                raise AuthStoreError(
                    _auth_store_error_message(keyring=keyring, error=error)
                ) from error
    secret_store.clear_file_store_secret(provider)
    return compute_provider_readiness(provider)


def _provider_env_key(provider: ProviderName) -> str:
    return secret_store.provider_env_key(provider)


__all__ = [
    "AuthStoreError",
    "AuthStorageKind",
    "LocalSecretStoreStatus",
    "PROVIDER_SECRET_ENV_KEYS",
    "SECRET_FILE_PATH",
    "ProviderAuthStatus",
    "ProviderName",
    "ProviderSecretValidationError",
    "clear_provider_secret",
    "get_provider_auth_status",
    "get_local_secret_store_status",
    "list_provider_auth_statuses",
    "resolve_provider_secret",
    "set_provider_secret",
]
