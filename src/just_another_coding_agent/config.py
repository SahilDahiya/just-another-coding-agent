"""Persistent runtime configuration stored at ~/.jaca/config.json."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CONFIG_DIR = Path.home() / ".jaca"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def apply_config_to_env(config: dict[str, str]) -> None:
    env_keys = {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
    }
    for key in env_keys:
        if key in config and key not in os.environ:
            os.environ[key] = config[key]


def save_provider_config(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> None:
    config = load_config()
    if provider == "ollama":
        if base_url:
            config["OLLAMA_BASE_URL"] = base_url
        if api_key:
            config["OLLAMA_API_KEY"] = api_key
    elif provider == "openai":
        if api_key:
            config["OPENAI_API_KEY"] = api_key
        if base_url:
            config["OPENAI_BASE_URL"] = base_url
    elif provider == "anthropic":
        if api_key:
            config["ANTHROPIC_API_KEY"] = api_key
    config["default_provider"] = provider
    save_config(config)


__all__ = [
    "apply_config_to_env",
    "load_config",
    "save_config",
    "save_provider_config",
]
