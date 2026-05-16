from __future__ import annotations

import json
import sys
from io import StringIO
from types import SimpleNamespace

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.config import load_mcp_server_configs


def _write_mcp_config(tmp_path) -> None:
    config_dir = tmp_path / ".jaca"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "linear": {
                        "transport": {
                            "type": "streamable_http",
                            "url": "https://mcp.linear.app/mcp",
                            "oauth": {"type": "oauth", "callback_port": 1456},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_jaca_mcp_login_uses_configured_server(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_mcp_config(tmp_path)
    seen = {}

    async def fake_login(config, *, auth_url_handler, connect):
        seen["server_id"] = config.server_id
        seen["url"] = config.transport.url
        await auth_url_handler("https://auth.example.test/login")
        return SimpleNamespace(server_id=config.server_id)

    monkeypatch.setattr(entry, "login_mcp_oauth_server", fake_login)
    output = StringIO()

    exit_code = entry.main(["mcp", "login", "linear"], output_stream=output)

    assert exit_code == 0
    assert seen == {
        "server_id": "linear",
        "url": "https://mcp.linear.app/mcp",
    }
    assert "https://auth.example.test/login" in output.getvalue()
    assert "Logged in MCP server linear." in output.getvalue()


def test_jaca_mcp_login_uses_oauth_sized_initialize_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_mcp_config(tmp_path)
    captured = {}

    class FakeMCPServerStreamableHTTP:
        def __init__(
            self,
            url,
            *,
            http_client,
            id,
            tool_prefix,
            timeout,
            read_timeout,
            allow_sampling,
            max_retries,
        ):
            captured["url"] = url
            captured["timeout"] = timeout
            captured["read_timeout"] = read_timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        sys.modules["pydantic_ai.mcp"],
        "MCPServerStreamableHTTP",
        FakeMCPServerStreamableHTTP,
    )

    async def fake_login(config, *, auth_url_handler, connect):
        await auth_url_handler("https://auth.example.test/login")
        await connect(object())
        return SimpleNamespace(server_id=config.server_id)

    monkeypatch.setattr(entry, "login_mcp_oauth_server", fake_login)

    exit_code = entry.main(["mcp", "login", "linear"], output_stream=StringIO())

    assert exit_code == 0
    assert captured == {
        "url": "https://mcp.linear.app/mcp",
        "timeout": 300.0,
        "read_timeout": 300.0,
    }


def test_jaca_mcp_logout_clears_configured_server_credentials(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_mcp_config(tmp_path)
    seen = {}

    def fake_clear(config):
        seen["server_id"] = config.server_id

    monkeypatch.setattr(entry, "clear_mcp_oauth_credentials", fake_clear)
    output = StringIO()

    exit_code = entry.main(["mcp", "logout", "linear"], output_stream=output)

    assert exit_code == 0
    assert seen == {"server_id": "linear"}
    assert output.getvalue() == "Logged out MCP server linear.\n"


def test_jaca_mcp_login_fails_for_unknown_server(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_mcp_config(tmp_path)
    output = StringIO()

    exit_code = entry.main(["mcp", "login", "missing"], output_stream=output)

    assert exit_code == 1
    assert output.getvalue() == "Error: Unknown MCP server: missing\n"


def test_jaca_mcp_add_oauth_writes_config_then_logs_in(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = {}

    async def fake_login(config, *, auth_url_handler, connect):
        seen["server_id"] = config.server_id
        seen["url"] = config.transport.url
        await auth_url_handler("https://auth.example.test/login")
        return SimpleNamespace(server_id=config.server_id)

    monkeypatch.setattr(entry, "login_mcp_oauth_server", fake_login)
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--oauth",
        ],
        output_stream=output,
    )

    assert exit_code == 0
    assert seen == {
        "server_id": "linear",
        "url": "https://mcp.linear.app/mcp",
    }
    servers = load_mcp_server_configs()
    assert set(servers) == {"linear"}
    assert servers["linear"].transport.type == "streamable_http"
    assert servers["linear"].transport.oauth is not None
    assert "https://auth.example.test/login" in output.getvalue()
    assert "Added and logged in MCP server linear." in output.getvalue()


def test_jaca_mcp_add_oauth_keeps_config_when_login_fails(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    async def fake_login(config, *, auth_url_handler, connect):
        raise RuntimeError("provider down")

    monkeypatch.setattr(entry, "login_mcp_oauth_server", fake_login)
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--oauth",
        ],
        output_stream=output,
    )

    assert exit_code == 1
    assert set(load_mcp_server_configs()) == {"linear"}
    assert output.getvalue() == (
        "MCP server linear was added, but OAuth login failed: provider down\n"
        "Run `jaca mcp login linear` to retry.\n"
    )


def test_jaca_mcp_add_bearer_env_writes_config_without_login(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--bearer-token-env-var",
            "LINEAR_MCP_TOKEN",
        ],
        output_stream=output,
    )

    assert exit_code == 0
    server = load_mcp_server_configs()["linear"]
    assert server.transport.type == "streamable_http"
    assert server.transport.bearer_token_env_var == "LINEAR_MCP_TOKEN"
    assert output.getvalue() == (
        "Added MCP server linear. Set LINEAR_MCP_TOKEN before use.\n"
    )


def test_jaca_mcp_add_stdio_writes_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "memory",
            "--",
            "npx",
            "-y",
            "@modelcontextprotocol/server-memory",
        ],
        output_stream=output,
    )

    assert exit_code == 0
    server = load_mcp_server_configs()["memory"]
    assert server.transport.type == "stdio"
    assert server.transport.command == "npx"
    assert server.transport.args == ["-y", "@modelcontextprotocol/server-memory"]
    assert output.getvalue() == "Added MCP server memory.\n"


def test_jaca_mcp_add_rejects_duplicate_server(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_mcp_config(tmp_path)
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--oauth",
        ],
        output_stream=output,
    )

    assert exit_code == 1
    assert output.getvalue() == "Error: MCP server already exists: linear\n"


def test_jaca_mcp_add_rejects_ambiguous_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    output = StringIO()

    exit_code = entry.main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--oauth",
            "--bearer-token-env-var",
            "LINEAR_MCP_TOKEN",
        ],
        output_stream=output,
    )

    assert exit_code == 1
    assert output.getvalue() == (
        "Error: --oauth and --bearer-token-env-var are mutually exclusive\n"
    )
