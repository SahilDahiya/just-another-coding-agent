from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import just_another_coding_agent.__main__ as entry


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
