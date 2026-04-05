from __future__ import annotations

import json
import stat
from pathlib import Path

from just_another_coding_agent.contracts.model_catalog import ProviderName

SECRET_STORE_SERVICE = "just-another-coding-agent"
SECRET_FILE_PATH = Path.home() / ".jaca" / "secrets.json"

PROVIDER_SECRET_ENV_KEYS: dict[ProviderName, str] = {
    "ollama": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}


class AuthStoreError(RuntimeError):
    pass


def missing_keyring_backend_message() -> str:
    return (
        "No supported OS keychain backend is available for local provider secret "
        "storage. Configure a supported system keychain and try again. On "
        "Linux/WSL, install and unlock a Secret Service backend such as "
        "gnome-keyring."
    )


def auth_store_error_message(*, keyring, error: Exception) -> str:
    no_keyring_error = getattr(keyring.errors, "NoKeyringError", None)
    if no_keyring_error is not None and isinstance(error, no_keyring_error):
        return missing_keyring_backend_message()
    if "No recommended backend was available" in str(error):
        return missing_keyring_backend_message()
    return str(error)


def provider_env_key(provider: ProviderName) -> str:
    try:
        return PROVIDER_SECRET_ENV_KEYS[provider]
    except KeyError as error:  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown provider: {provider}") from error


def get_keychain_secret(
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
    env_key = provider_env_key(provider)
    try:
        value = keyring.get_password(SECRET_STORE_SERVICE, env_key)
    except keyring.errors.KeyringError as error:
        if allow_missing_keychain:
            return None
        raise AuthStoreError(
            auth_store_error_message(keyring=keyring, error=error)
        ) from error
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def load_file_store() -> dict[str, str]:
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


def save_file_store(store: dict[str, str]) -> None:
    SECRET_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE_PATH.write_text(
        json.dumps(store, indent=2) + "\n",
        encoding="utf-8",
    )
    SECRET_FILE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def get_file_store_secret(provider: ProviderName) -> str | None:
    env_key = provider_env_key(provider)
    value = load_file_store().get(env_key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def set_file_store_secret(provider: ProviderName, secret: str) -> None:
    env_key = provider_env_key(provider)
    store = load_file_store()
    store[env_key] = secret
    save_file_store(store)


def clear_file_store_secret(provider: ProviderName) -> None:
    store = load_file_store()
    env_key = provider_env_key(provider)
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
    save_file_store(store)


def _load_keyring():
    try:
        import keyring
    except ImportError as error:  # pragma: no cover - dependency contract
        raise AuthStoreError(
            "Python dependency 'keyring' is required for local provider secret storage"
        ) from error

    return keyring


def load_keyring():
    return _load_keyring()


__all__ = [
    "AuthStoreError",
    "PROVIDER_SECRET_ENV_KEYS",
    "SECRET_FILE_PATH",
    "SECRET_STORE_SERVICE",
    "_load_keyring",
    "auth_store_error_message",
    "clear_file_store_secret",
    "get_file_store_secret",
    "get_keychain_secret",
    "load_keyring",
    "missing_keyring_backend_message",
    "provider_env_key",
    "set_file_store_secret",
]
