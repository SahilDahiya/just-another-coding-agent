from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.auth import OAuthToken

from just_another_coding_agent.auth import (
    clear_provider_secret,
    get_local_secret_store_status,
    get_mcp_server_auth_statuses,
    get_provider_auth_status,
    prepare_provider_secret_file,
    resolve_openai_codex_oauth_credentials,
    resolve_provider_secret,
    set_provider_secret,
)
from just_another_coding_agent.config import save_mcp_server_configs
from just_another_coding_agent.contracts.mcp import (
    McpOAuthConfig,
    McpServerConfig,
    McpStdioTransport,
    McpStreamableHttpTransport,
)
from just_another_coding_agent.mcp_oauth import McpOAuthTokenStorage


def test_set_and_resolve_provider_secret_uses_auth_file(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = set_provider_secret("openai", "test-key")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "file"
    assert status.env_key == "OPENAI_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("openai") == "test-key"
    assert auth_path.exists()


def test_get_provider_auth_status_prefers_environment(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    set_provider_secret("openai", "from-file")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")

    status = get_provider_auth_status("openai")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "env"
    assert status.env_key == "OPENAI_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("openai") == "from-env"


def test_clear_provider_secret_removes_file_value(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_provider_secret("anthropic", "test-key")

    status = clear_provider_secret("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "ANTHROPIC_API_KEY"
    assert status.reason == "missing_secret"
    assert resolve_provider_secret("anthropic") is None
    assert not auth_path.exists()


def test_set_provider_secret_rejects_blank() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        set_provider_secret("openai", "   ")


def test_local_secret_store_status_reports_auth_file_path(
    monkeypatch, tmp_path
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    status = get_local_secret_store_status()

    assert status.available is True
    assert status.message is None
    assert Path(status.file_store_path) == auth_path


def test_set_provider_secret_only_accepts_file_storage(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    with pytest.raises(ValueError, match="unknown auth storage"):
        set_provider_secret("openai", "test-key", storage="keychain")  # type: ignore[arg-type]


def test_prepare_provider_secret_file_creates_empty_auth_store(
    monkeypatch, tmp_path
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    prepared = prepare_provider_secret_file("openai")

    assert prepared.provider == "openai"
    assert prepared.env_key == "OPENAI_API_KEY"
    assert prepared.file_path == str(auth_path)
    assert prepared.created is True
    assert prepared.file_snippet == '{\n  "OPENAI_API_KEY": "..."\n}'
    assert prepared.entry_snippet == '"OPENAI_API_KEY": "..."'


async def test_get_mcp_server_auth_statuses_reports_configured_servers(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LINEAR_MCP_TOKEN", raising=False)
    oauth_config = McpServerConfig(
        server_id="linear",
        transport=McpStreamableHttpTransport(
            url="https://mcp.linear.app/mcp",
            oauth=McpOAuthConfig(),
        ),
    )
    bearer_config = McpServerConfig(
        server_id="github",
        transport=McpStreamableHttpTransport(
            url="https://mcp.github.example/mcp",
            bearer_token_env_var="GITHUB_MCP_TOKEN",
        ),
    )
    stdio_config = McpServerConfig(
        server_id="memory",
        transport=McpStdioTransport(command="npx"),
    )
    disabled_config = McpServerConfig(
        server_id="disabled_linear",
        transport=McpStreamableHttpTransport(
            url="https://mcp.linear.app/mcp",
            oauth=McpOAuthConfig(),
        ),
        enabled=False,
    )
    save_mcp_server_configs(
        {
            "linear": oauth_config,
            "github": bearer_config,
            "memory": stdio_config,
            "disabled_linear": disabled_config,
        }
    )
    await McpOAuthTokenStorage.from_server_config(oauth_config).set_tokens(
        OAuthToken(access_token="linear-token", token_type="Bearer")
    )
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "github-token")

    statuses = get_mcp_server_auth_statuses()

    assert [status.model_dump(mode="json") for status in statuses] == [
        {
            "server_id": "disabled_linear",
            "transport_type": "streamable_http",
            "enabled": False,
            "auth_kind": "oauth",
            "configured": False,
            "reason": "disabled",
            "env_var": None,
        },
        {
            "server_id": "github",
            "transport_type": "streamable_http",
            "enabled": True,
            "auth_kind": "bearer_env",
            "configured": True,
            "reason": "ok",
            "env_var": "GITHUB_MCP_TOKEN",
        },
        {
            "server_id": "linear",
            "transport_type": "streamable_http",
            "enabled": True,
            "auth_kind": "oauth",
            "configured": True,
            "reason": "ok",
            "env_var": None,
        },
        {
            "server_id": "memory",
            "transport_type": "stdio",
            "enabled": True,
            "auth_kind": "none",
            "configured": True,
            "reason": "no_auth_required",
            "env_var": None,
        },
    ]


def test_get_mcp_server_auth_statuses_reports_missing_auth(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LINEAR_MCP_TOKEN", raising=False)
    save_mcp_server_configs(
        {
            "linear": McpServerConfig(
                server_id="linear",
                transport=McpStreamableHttpTransport(
                    url="https://mcp.linear.app/mcp",
                    oauth=McpOAuthConfig(),
                ),
            ),
            "github": McpServerConfig(
                server_id="github",
                transport=McpStreamableHttpTransport(
                    url="https://mcp.github.example/mcp",
                    bearer_token_env_var="LINEAR_MCP_TOKEN",
                ),
            ),
        }
    )

    statuses = get_mcp_server_auth_statuses()

    assert [status.model_dump(mode="json") for status in statuses] == [
        {
            "server_id": "github",
            "transport_type": "streamable_http",
            "enabled": True,
            "auth_kind": "bearer_env",
            "configured": False,
            "reason": "missing_bearer_env",
            "env_var": "LINEAR_MCP_TOKEN",
        },
        {
            "server_id": "linear",
            "transport_type": "streamable_http",
            "enabled": True,
            "auth_kind": "oauth",
            "configured": False,
            "reason": "oauth_login_required",
            "env_var": None,
        },
    ]


def test_prepare_provider_secret_file_keeps_existing_auth_store(
    monkeypatch, tmp_path
) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"ANTHROPIC_API_KEY":"existing"}')
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    prepared = prepare_provider_secret_file("anthropic")

    assert prepared.provider == "anthropic"
    assert prepared.env_key == "ANTHROPIC_API_KEY"
    assert prepared.file_path == str(auth_path)
    assert prepared.created is False
    assert prepared.file_snippet == '{\n  "ANTHROPIC_API_KEY": "..."\n}'
    assert prepared.entry_snippet == '"ANTHROPIC_API_KEY": "..."'
    assert auth_path.read_text() == '{"ANTHROPIC_API_KEY":"existing"}'


def test_get_provider_auth_status_requires_openai_secret_even_with_local_base_url(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        tmp_path / "auth.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")

    status = get_provider_auth_status("openai")

    assert status.provider == "openai"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.reason == "missing_secret"


def test_get_provider_auth_status_marks_anthropic_missing_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        tmp_path / "auth.json",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = get_provider_auth_status("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "ANTHROPIC_API_KEY"
    assert status.reason == "missing_secret"


@pytest.mark.asyncio
async def test_resolve_openai_codex_oauth_credentials_prefers_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCESS_TOKEN", "env-access")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_REFRESH_TOKEN", "env-refresh")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_EXPIRES_AT", "4102444800000")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCOUNT_ID", "acct-env")
    monkeypatch.setattr(
        "just_another_coding_agent.auth.get_openai_codex_credentials",
        lambda: None,
    )

    credentials = await resolve_openai_codex_oauth_credentials()

    assert credentials is not None
    assert credentials.access == "env-access"
    assert credentials.refresh == "env-refresh"
    assert credentials.account_id == "acct-env"
