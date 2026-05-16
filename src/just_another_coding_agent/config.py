"""Persistent runtime configuration stored at ~/.jaca/config.json."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from just_another_coding_agent.contracts.mcp import McpServerConfig
from just_another_coding_agent.contracts.model_catalog import default_model_for_provider

CONFIG_DIR = Path.home() / ".jaca"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_MODEL = default_model_for_provider("openai")
_MCP_SERVER_CONFIG_MAP_ADAPTER = TypeAdapter(dict[str, McpServerConfig])


def _config_dir() -> Path:
    return Path.home() / ".jaca"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    config_path = _config_path()
    if not config_path.exists():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON in {config_path}: {error}") from error
    except OSError as error:
        raise RuntimeError(f"Could not read {config_path}: {error}") from error
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Invalid config in {config_path}: expected JSON object")
    return loaded


def save_config(config: Mapping[str, Any]) -> None:
    config_dir = _config_dir()
    config_path = _config_path()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(dict(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_mcp_server_configs(
    config: Mapping[str, Any] | None = None,
) -> dict[str, McpServerConfig]:
    loaded_config = load_config() if config is None else config
    raw_servers = loaded_config.get("mcp_servers")
    if raw_servers is None:
        return {}
    if not isinstance(raw_servers, dict):
        raise TypeError("mcp_servers must be a JSON object keyed by server id")
    server_values: dict[str, Any] = {}
    for server_id, raw_server in raw_servers.items():
        if not isinstance(server_id, str):
            raise TypeError("mcp_servers keys must be strings")
        if not isinstance(raw_server, dict):
            raise TypeError(f"mcp_servers.{server_id} must be a JSON object")
        server_values[server_id] = {"server_id": server_id, **raw_server}
    return _MCP_SERVER_CONFIG_MAP_ADAPTER.validate_python(server_values)


def save_mcp_server_configs(
    servers: Mapping[str, McpServerConfig],
) -> None:
    serialized_servers: dict[str, dict[str, Any]] = {}
    for server_id, server_config in servers.items():
        if server_id != server_config.server_id:
            raise ValueError("MCP server config key must match server_id")
        serialized_servers[server_id] = server_config.model_dump(
            mode="json",
            exclude_none=True,
        )

    config = load_config()
    if serialized_servers:
        config["mcp_servers"] = serialized_servers
    else:
        config.pop("mcp_servers", None)
    save_config(config)


def apply_config_to_env(config: Mapping[str, Any]) -> None:
    env_keys = {
        "OPENAI_BASE_URL",
    }
    for key in env_keys:
        if key in config and key not in os.environ:
            os.environ[key] = config[key]


def _has_explicit_trace_mode(env: Mapping[str, str]) -> bool:
    return env.get("JACA_TRACE_MODE", "").strip() != ""


def apply_trace_mode_to_env(config: Mapping[str, Any]) -> None:
    if _has_explicit_trace_mode(os.environ):
        return
    os.environ.pop("JACA_TRACE_MODE", None)
    raw_trace_mode = config.get("trace_mode", "")
    if not isinstance(raw_trace_mode, str):
        raise RuntimeError(
            "Invalid trace_mode in ~/.jaca/config.json: expected off, local, or logfire"
        )
    trace_mode = raw_trace_mode.strip().lower()
    if trace_mode == "":
        return
    if trace_mode == "off":
        os.environ.pop("JACA_TRACE_MODE", None)
        return
    if trace_mode in {"local", "logfire"}:
        os.environ["JACA_TRACE_MODE"] = trace_mode
        return
    raise RuntimeError(
        "Invalid trace_mode in ~/.jaca/config.json: expected off, local, or logfire"
    )


def resolve_default_model(config: Mapping[str, Any]) -> str:
    env_model = os.environ.get("JACA_MODEL", "").strip()
    if env_model:
        return env_model
    raw_saved_model = config.get("default_model", "")
    saved_model = raw_saved_model.strip() if isinstance(raw_saved_model, str) else ""
    if saved_model:
        return saved_model
    return DEFAULT_MODEL


__all__ = [
    "apply_config_to_env",
    "apply_trace_mode_to_env",
    "DEFAULT_MODEL",
    "load_config",
    "load_mcp_server_configs",
    "resolve_default_model",
    "save_config",
    "save_mcp_server_configs",
]
