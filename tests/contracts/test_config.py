from __future__ import annotations

import json
import os

import pytest
from pydantic import ValidationError

from just_another_coding_agent.__main__ import _build_subprocess_env
from just_another_coding_agent.config import (
    apply_trace_mode_to_env,
    load_config,
    load_mcp_server_configs,
    save_mcp_server_configs,
)
from just_another_coding_agent.contracts.mcp import (
    McpServerConfig,
    McpStreamableHttpTransport,
)


def test_apply_trace_mode_to_env_preserves_explicit_env_when_config_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")

    apply_trace_mode_to_env({})

    assert os.environ["JACA_TRACE_MODE"] == "logfire"


def test_apply_trace_mode_to_env_uses_config_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("JACA_TRACE_MODE", raising=False)

    apply_trace_mode_to_env({"trace_mode": "logfire"})

    assert os.environ["JACA_TRACE_MODE"] == "logfire"


def test_apply_trace_mode_to_env_treats_blank_env_as_unset(monkeypatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "")

    apply_trace_mode_to_env({"trace_mode": "logfire"})

    assert os.environ["JACA_TRACE_MODE"] == "logfire"


def test_apply_trace_mode_to_env_allows_config_off_when_env_is_blank(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "")

    apply_trace_mode_to_env({"trace_mode": "off"})

    assert "JACA_TRACE_MODE" not in os.environ


def test_build_subprocess_env_preserves_explicit_trace_mode(monkeypatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")

    env = _build_subprocess_env({})

    assert env["JACA_TRACE_MODE"] == "logfire"


def test_build_subprocess_env_uses_config_trace_mode_when_env_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JACA_TRACE_MODE", raising=False)

    env = _build_subprocess_env({"trace_mode": "logfire"})

    assert env["JACA_TRACE_MODE"] == "logfire"


def test_build_subprocess_env_treats_blank_trace_mode_env_as_unset(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "")

    env = _build_subprocess_env({"trace_mode": "logfire"})

    assert env["JACA_TRACE_MODE"] == "logfire"


def test_build_subprocess_env_allows_config_off_when_env_is_blank(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "")

    env = _build_subprocess_env({"trace_mode": "off"})

    assert "JACA_TRACE_MODE" not in env


def test_load_config_fails_hard_for_invalid_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".jaca"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid JSON"):
        load_config()


def test_load_mcp_server_configs_returns_empty_when_missing() -> None:
    assert load_mcp_server_configs({}) == {}


def test_load_mcp_server_configs_parses_typed_server_map() -> None:
    servers = load_mcp_server_configs(
        {
            "mcp_servers": {
                "linear": {
                    "transport": {
                        "type": "streamable_http",
                        "url": "https://mcp.linear.app/mcp",
                        "bearer_token_env_var": "LINEAR_MCP_TOKEN",
                    },
                    "required": True,
                    "enabled_tools": ["create-issue"],
                    "tools": {
                        "create-issue": {
                            "approval_mode": "prompt",
                        },
                    },
                },
            },
        }
    )

    assert servers == {
        "linear": McpServerConfig(
            server_id="linear",
            transport=McpStreamableHttpTransport(
                url="https://mcp.linear.app/mcp",
                bearer_token_env_var="LINEAR_MCP_TOKEN",
            ),
            required=True,
            enabled_tools=["create-issue"],
            tools={"create-issue": {"approval_mode": "prompt"}},
        )
    }


def test_load_mcp_server_configs_fails_hard_for_bad_shape() -> None:
    with pytest.raises(TypeError, match="mcp_servers"):
        load_mcp_server_configs({"mcp_servers": []})

    with pytest.raises(ValidationError):
        load_mcp_server_configs(
            {
                "mcp_servers": {
                    "Linear": {
                        "transport": {
                            "type": "streamable_http",
                            "url": "https://mcp.linear.app/mcp",
                        },
                    },
                },
            }
        )


def test_save_mcp_server_configs_merges_config_without_dropping_existing_keys(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".jaca"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps({"default_model": "openai-responses:gpt-5.4"}),
        encoding="utf-8",
    )

    save_mcp_server_configs(
        {
            "linear": McpServerConfig(
                server_id="linear",
                transport=McpStreamableHttpTransport(
                    url="https://mcp.linear.app/mcp",
                ),
            ),
        }
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "default_model": "openai-responses:gpt-5.4",
        "mcp_servers": {
            "linear": {
                "server_id": "linear",
                "transport": {
                    "type": "streamable_http",
                    "url": "https://mcp.linear.app/mcp",
                },
                "enabled": True,
                "required": False,
                "tools": {},
            },
        },
    }
