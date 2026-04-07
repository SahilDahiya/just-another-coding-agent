from __future__ import annotations

import json
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile

from just_another_coding_agent.contracts.model_catalog import ProviderName

AUTH_FILE_PATH = Path.home() / ".jaca" / "auth.json"
SECRET_FILE_PATH = AUTH_FILE_PATH

PROVIDER_SECRET_ENV_KEYS: dict[ProviderName, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class AuthStoreError(RuntimeError):
    pass


def provider_env_key(provider: ProviderName) -> str:
    try:
        return PROVIDER_SECRET_ENV_KEYS[provider]
    except KeyError as error:  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown provider: {provider}") from error


def load_file_store() -> dict[str, str]:
    if not AUTH_FILE_PATH.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise AuthStoreError(f"Invalid auth file at {AUTH_FILE_PATH}") from error
    if not isinstance(data, dict):
        raise AuthStoreError(f"Invalid auth file at {AUTH_FILE_PATH}")
    store: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise AuthStoreError(f"Invalid auth file at {AUTH_FILE_PATH}")
        store[key] = value
    return store


def save_file_store(store: dict[str, str]) -> None:
    AUTH_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=AUTH_FILE_PATH.parent,
            delete=False,
        ) as handle:
            json.dump(store, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temp_path.replace(AUTH_FILE_PATH)
    except OSError as error:
        raise AuthStoreError(
            f"Failed to write auth file at {AUTH_FILE_PATH}"
        ) from error


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
            AUTH_FILE_PATH.unlink(missing_ok=True)
        except OSError as error:
            raise AuthStoreError(
                f"Failed to remove auth file at {AUTH_FILE_PATH}"
            ) from error
        return
    save_file_store(store)


__all__ = [
    "AUTH_FILE_PATH",
    "AuthStoreError",
    "PROVIDER_SECRET_ENV_KEYS",
    "SECRET_FILE_PATH",
    "clear_file_store_secret",
    "get_file_store_secret",
    "load_file_store",
    "provider_env_key",
    "save_file_store",
    "set_file_store_secret",
]
