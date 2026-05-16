from __future__ import annotations

import json

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from just_another_coding_agent.contracts.mcp import (
    McpOAuthConfig,
    McpServerConfig,
    McpStreamableHttpTransport,
)
from just_another_coding_agent.mcp_oauth import (
    McpOAuthTokenStorage,
    clear_mcp_oauth_credentials,
    mcp_oauth_config_fingerprint,
)
from just_another_coding_agent.oauth_store import (
    get_mcp_oauth_record,
    load_oauth_store,
)


def _linear_config(url: str = "https://mcp.linear.app/mcp") -> McpServerConfig:
    return McpServerConfig(
        server_id="linear",
        transport=McpStreamableHttpTransport(
            url=url,
            oauth=McpOAuthConfig(callback_port=1456),
        ),
    )


def test_mcp_oauth_config_fingerprint_changes_with_server_url() -> None:
    first = mcp_oauth_config_fingerprint(_linear_config())
    second = mcp_oauth_config_fingerprint(
        _linear_config("https://mcp.linear.app/other")
    )

    assert first != second
    assert len(first) == 64


async def test_mcp_oauth_token_storage_round_trips_sdk_shapes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = _linear_config()
    storage = McpOAuthTokenStorage.from_server_config(config)
    tokens = OAuthToken(
        access_token="access-token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="refresh-token",
        scope="read write",
    )
    client_info = OAuthClientInformationFull(
        redirect_uris=["http://127.0.0.1:1456/mcp/oauth/callback"],
        token_endpoint_auth_method="none",
        client_id="client-id",
    )

    await storage.set_tokens(tokens)
    await storage.set_client_info(client_info)

    assert await storage.get_tokens() == tokens
    assert await storage.get_client_info() == client_info
    record = get_mcp_oauth_record(
        server_id="linear",
        config_fingerprint=mcp_oauth_config_fingerprint(config),
    )
    assert record is not None
    assert record.tokens == tokens.model_dump(mode="json", exclude_none=True)
    assert record.client_info == client_info.model_dump(
        mode="json",
        exclude_none=True,
    )

    saved = json.loads((tmp_path / ".jaca" / "oauth.json").read_text())
    assert list(saved["mcp_servers"]) == [
        f"linear:{mcp_oauth_config_fingerprint(config)}"
    ]


async def test_mcp_oauth_token_storage_uses_configured_client_id(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = McpServerConfig(
        server_id="linear",
        transport=McpStreamableHttpTransport(
            url="https://mcp.linear.app/mcp",
            oauth=McpOAuthConfig(callback_port=1456, client_id="linear-client"),
        ),
    )

    client_info = await McpOAuthTokenStorage.from_server_config(
        config
    ).get_client_info()

    assert client_info is not None
    assert client_info.client_id == "linear-client"
    assert [str(uri) for uri in client_info.redirect_uris or []] == [
        "http://127.0.0.1:1456/mcp/oauth/callback"
    ]


def test_clear_mcp_oauth_credentials_removes_only_matching_fingerprint(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = _linear_config()
    other_config = _linear_config("https://mcp.linear.app/other")

    first = McpOAuthTokenStorage.from_server_config(config)
    second = McpOAuthTokenStorage.from_server_config(other_config)

    import asyncio

    asyncio.run(
        first.set_tokens(
            OAuthToken(access_token="first", token_type="Bearer", expires_in=3600)
        )
    )
    asyncio.run(
        second.set_tokens(
            OAuthToken(access_token="second", token_type="Bearer", expires_in=3600)
        )
    )

    clear_mcp_oauth_credentials(config)

    store = load_oauth_store()
    assert (
        get_mcp_oauth_record(
            server_id="linear",
            config_fingerprint=mcp_oauth_config_fingerprint(config),
            store=store,
        )
        is None
    )
    assert (
        get_mcp_oauth_record(
            server_id="linear",
            config_fingerprint=mcp_oauth_config_fingerprint(other_config),
            store=store,
        )
        is not None
    )
