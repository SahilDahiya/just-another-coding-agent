from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from just_another_coding_agent.contracts.auth import (
    AuthStorageKind,
    LocalSecretStoreStatus,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import ProviderName

SECRET_STORE_SERVICE = "just-another-coding-agent"
SECRET_FILE_PATH = Path.home() / ".jaca" / "secrets.json"

PROVIDER_SECRET_ENV_KEYS: dict[ProviderName, str] = {
    "ollama": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}


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
    keychain_error: AuthStoreError | None = None
    try:
        keychain_value = _get_keychain_secret(
            provider,
            allow_missing_keychain=allow_missing_keychain,
        )
    except AuthStoreError as error:
        keychain_error = error
        keychain_value = None
    if keychain_value:
        return keychain_value
    file_store_value = _get_file_store_secret(provider)
    if file_store_value:
        return file_store_value
    if keychain_error is not None and not allow_missing_keychain:
        raise keychain_error
    return None


def get_provider_auth_status(provider: ProviderName) -> ProviderAuthStatus:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return ProviderAuthStatus(
            provider=provider,
            configured=True,
            source="env",
            env_key=env_key,
        )

    keychain_value = _get_keychain_secret(provider, allow_missing_keychain=True)
    if keychain_value:
        return ProviderAuthStatus(
            provider=provider,
            configured=True,
            source="keychain",
            env_key=env_key,
        )

    file_store_value = _get_file_store_secret(provider)
    if file_store_value:
        return ProviderAuthStatus(
            provider=provider,
            configured=True,
            source="file",
            env_key=env_key,
        )

    return ProviderAuthStatus(
        provider=provider,
        configured=False,
        source="none",
        env_key=env_key,
    )


def list_provider_auth_statuses() -> list[ProviderAuthStatus]:
    return [get_provider_auth_status(provider) for provider in PROVIDER_SECRET_ENV_KEYS]


def get_local_secret_store_status() -> LocalSecretStoreStatus:
    try:
        keyring = _load_keyring()
    except AuthStoreError as error:
        return LocalSecretStoreStatus(available=False, message=str(error))

    backend = keyring.get_keyring()
    priority = getattr(backend, "priority", 0)
    if priority <= 0:
        return LocalSecretStoreStatus(
            available=False,
            message=_missing_keyring_backend_message(),
            file_store_path=str(SECRET_FILE_PATH),
        )
    return LocalSecretStoreStatus(
        available=True,
        file_store_path=str(SECRET_FILE_PATH),
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
        keyring = _load_keyring()
        try:
            keyring.set_password(SECRET_STORE_SERVICE, env_key, trimmed)
        except keyring.errors.KeyringError as error:
            raise AuthStoreError(
                _auth_store_error_message(keyring=keyring, error=error)
            ) from error
    elif storage == "file":
        _set_file_store_secret(provider, trimmed)
    else:  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown auth storage: {storage}")
    return get_provider_auth_status(provider)


def clear_provider_secret(provider: ProviderName) -> ProviderAuthStatus:
    keyring = None
    try:
        keyring = _load_keyring()
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
    _clear_file_store_secret(provider)
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


def _load_file_store() -> dict[str, str]:
    if not SECRET_FILE_PATH.exists():
        return {}
    try:
        data = json.loads(SECRET_FILE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise AuthStoreError(
            f"Invalid local secret file at {SECRET_FILE_PATH}"
        ) from error
    if not isinstance(data, dict):
        raise AuthStoreError(f"Invalid local secret file at {SECRET_FILE_PATH}")
    store: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise AuthStoreError(f"Invalid local secret file at {SECRET_FILE_PATH}")
        store[key] = value
    return store


def _save_file_store(store: dict[str, str]) -> None:
    SECRET_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE_PATH.write_text(
        json.dumps(store, indent=2) + "\n",
        encoding="utf-8",
    )
    SECRET_FILE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _get_file_store_secret(provider: ProviderName) -> str | None:
    env_key = _provider_env_key(provider)
    value = _load_file_store().get(env_key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _set_file_store_secret(provider: ProviderName, secret: str) -> None:
    env_key = _provider_env_key(provider)
    store = _load_file_store()
    store[env_key] = secret
    _save_file_store(store)


def _clear_file_store_secret(provider: ProviderName) -> None:
    store = _load_file_store()
    env_key = _provider_env_key(provider)
    if env_key not in store:
        return
    del store[env_key]
    if not store:
        try:
            SECRET_FILE_PATH.unlink(missing_ok=True)
        except OSError as error:
            raise AuthStoreError(
                f"Failed to remove local secret file at {SECRET_FILE_PATH}"
            ) from error
        return
    _save_file_store(store)


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
