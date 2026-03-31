"""Persistent runtime configuration stored at ~/.jaca/config.json."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from just_another_coding_agent.contracts.model_catalog import default_model_for_provider

CONFIG_DIR = Path.home() / ".jaca"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_MODEL = default_model_for_provider("ollama")


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
        "OPENAI_BASE_URL",
        "OLLAMA_BASE_URL",
    }
    for key in env_keys:
        if key in config and key not in os.environ:
            os.environ[key] = config[key]


def apply_trace_mode_to_env(config: dict[str, str]) -> None:
    trace_mode = config.get("trace_mode", "").strip().lower()
    if trace_mode in {"", "off"}:
        os.environ.pop("JACA_TRACE_MODE", None)
        return
    if trace_mode in {"local", "logfire"}:
        os.environ["JACA_TRACE_MODE"] = trace_mode
        return
    raise RuntimeError(
        "Invalid trace_mode in ~/.jaca/config.json: expected off, local, or logfire"
    )


def resolve_default_model(config: dict[str, str]) -> str:
    env_model = os.environ.get("JACA_MODEL", "").strip()
    if env_model:
        return env_model
    saved_model = config.get("default_model", "").strip()
    if saved_model:
        return saved_model
    return DEFAULT_MODEL


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
    elif provider == "openai":
        if base_url:
            config["OPENAI_BASE_URL"] = base_url
    elif provider in {"anthropic", "github"}:
        pass
    config["default_provider"] = provider
    save_config(config)


__all__ = [
    "apply_config_to_env",
    "apply_trace_mode_to_env",
    "DEFAULT_MODEL",
    "load_config",
    "resolve_default_model",
    "save_config",
    "save_provider_config",
]
