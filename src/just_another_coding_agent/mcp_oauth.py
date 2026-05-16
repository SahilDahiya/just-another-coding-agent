from __future__ import annotations

import asyncio
import hashlib
import html
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.oauth2 import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from just_another_coding_agent.contracts.mcp import (
    McpOAuthConfig,
    McpServerConfig,
    McpStreamableHttpTransport,
)
from just_another_coding_agent.oauth_store import (
    McpOAuthRecord,
    clear_mcp_oauth_record,
    get_mcp_oauth_record,
    set_mcp_oauth_record,
)

_CALLBACK_HOST = "127.0.0.1"
_CALLBACK_PATH = "/mcp/oauth/callback"


class McpOAuthError(RuntimeError):
    pass


class McpOAuthLoginRequiredError(McpOAuthError):
    pass


@dataclass(frozen=True)
class McpOAuthLoginResult:
    server_id: str
    authenticated: bool


def mcp_oauth_config_fingerprint(config: McpServerConfig) -> str:
    transport = _oauth_transport(config)
    payload = {
        "server_id": config.server_id,
        "transport": {
            "type": transport.type,
            "url": transport.url,
            "oauth": transport.oauth.model_dump(mode="json", exclude_none=True),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class McpOAuthTokenStorage(TokenStorage):
    def __init__(
        self,
        *,
        server_id: str,
        config_fingerprint: str,
        initial_client_info: OAuthClientInformationFull | None = None,
    ) -> None:
        self.server_id = server_id
        self.config_fingerprint = config_fingerprint
        self.initial_client_info = initial_client_info

    @classmethod
    def from_server_config(cls, config: McpServerConfig) -> "McpOAuthTokenStorage":
        transport = _oauth_transport(config)
        oauth = _oauth_config(transport)
        return cls(
            server_id=config.server_id,
            config_fingerprint=mcp_oauth_config_fingerprint(config),
            initial_client_info=_initial_client_info(oauth),
        )

    async def get_tokens(self) -> OAuthToken | None:
        record = self._record()
        if record is None or record.tokens is None:
            return None
        return OAuthToken.model_validate(record.tokens)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        record = self._record()
        set_mcp_oauth_record(
            McpOAuthRecord(
                server_id=self.server_id,
                config_fingerprint=self.config_fingerprint,
                tokens=tokens.model_dump(mode="json", exclude_none=True),
                client_info=record.client_info if record is not None else None,
            )
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        record = self._record()
        if record is None or record.client_info is None:
            return self.initial_client_info
        return OAuthClientInformationFull.model_validate(record.client_info)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        record = self._record()
        set_mcp_oauth_record(
            McpOAuthRecord(
                server_id=self.server_id,
                config_fingerprint=self.config_fingerprint,
                tokens=record.tokens if record is not None else None,
                client_info=client_info.model_dump(mode="json", exclude_none=True),
            )
        )

    def _record(self) -> McpOAuthRecord | None:
        return get_mcp_oauth_record(
            server_id=self.server_id,
            config_fingerprint=self.config_fingerprint,
        )


def build_mcp_oauth_http_client(
    config: McpServerConfig,
    *,
    redirect_handler: Callable[[str], Awaitable[None]] | None = None,
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]] | None = None,
) -> httpx.AsyncClient:
    transport = _oauth_transport(config)
    oauth = _oauth_config(transport)
    auth = OAuthClientProvider(
        server_url=transport.url,
        client_metadata=_oauth_client_metadata(oauth),
        storage=McpOAuthTokenStorage.from_server_config(config),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    return httpx.AsyncClient(auth=auth)


def require_mcp_oauth_login(config: McpServerConfig) -> None:
    storage = McpOAuthTokenStorage.from_server_config(config)
    record = get_mcp_oauth_record(
        server_id=storage.server_id,
        config_fingerprint=storage.config_fingerprint,
    )
    if record is None or record.tokens is None:
        raise McpOAuthLoginRequiredError(
            f"MCP server '{config.server_id}' requires OAuth login"
        )


async def login_mcp_oauth_server(
    config: McpServerConfig,
    *,
    auth_url_handler: Callable[[str], Awaitable[None]],
    connect: Callable[[httpx.AsyncClient], Awaitable[None]],
) -> McpOAuthLoginResult:
    transport = _oauth_transport(config)
    oauth = _oauth_config(transport)
    async with build_mcp_oauth_http_client(
        config,
        redirect_handler=auth_url_handler,
        callback_handler=lambda: wait_for_mcp_oauth_callback(oauth),
    ) as client:
        await connect(client)
    require_mcp_oauth_login(config)
    return McpOAuthLoginResult(server_id=config.server_id, authenticated=True)


def clear_mcp_oauth_credentials(config: McpServerConfig) -> None:
    storage = McpOAuthTokenStorage.from_server_config(config)
    clear_mcp_oauth_record(
        server_id=storage.server_id,
        config_fingerprint=storage.config_fingerprint,
    )


async def wait_for_mcp_oauth_callback(
    oauth: McpOAuthConfig,
    *,
    timeout_seconds: float = 300.0,
) -> tuple[str, str | None]:
    result: asyncio.Future[tuple[str, str | None]] = (
        asyncio.get_running_loop().create_future()
    )

    async def _handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            try:
                method, target, _ = request_line.decode("ascii").strip().split(" ", 2)
            except ValueError:
                await _write_http_response(
                    writer,
                    400,
                    _oauth_page("Invalid callback request."),
                )
                return
            if method != "GET":
                await _write_http_response(
                    writer, 405, _oauth_page("Method not allowed.")
                )
                return
            parsed = urlparse(target)
            if parsed.path != _CALLBACK_PATH:
                await _write_http_response(
                    writer,
                    404,
                    _oauth_page("Unknown callback path."),
                )
                return
            while True:
                header_line = await reader.readline()
                if not header_line or header_line in (b"\r\n", b"\n"):
                    break
            query = parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            state = query.get("state", [None])[0]
            if not code:
                await _write_http_response(
                    writer,
                    400,
                    _oauth_page("Missing authorization code."),
                )
                return
            if not result.done():
                result.set_result((code, state))
            await _write_http_response(
                writer,
                200,
                _oauth_page("MCP login complete. Return to JACA."),
            )
        except Exception as error:
            if not result.done():
                result.set_exception(error)
            try:
                await _write_http_response(
                    writer,
                    500,
                    _oauth_page("MCP login failed. Return to JACA."),
                )
            except Exception:
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        _handle_client,
        host=_CALLBACK_HOST,
        port=oauth.callback_port,
    )
    try:
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    finally:
        server.close()
        await server.wait_closed()


def _oauth_client_metadata(oauth: McpOAuthConfig) -> OAuthClientMetadata:
    scope = " ".join(oauth.scopes) if oauth.scopes else None
    return OAuthClientMetadata(
        redirect_uris=[
            f"http://{_CALLBACK_HOST}:{oauth.callback_port}{_CALLBACK_PATH}"
        ],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
        client_name="JACA",
    )


def _initial_client_info(oauth: McpOAuthConfig) -> OAuthClientInformationFull | None:
    if oauth.client_id is None:
        return None
    return OAuthClientInformationFull(
        redirect_uris=[
            f"http://{_CALLBACK_HOST}:{oauth.callback_port}{_CALLBACK_PATH}"
        ],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(oauth.scopes) if oauth.scopes else None,
        client_name="JACA",
        client_id=oauth.client_id,
    )


def _oauth_transport(config: McpServerConfig) -> McpStreamableHttpTransport:
    transport = config.transport
    if not isinstance(transport, McpStreamableHttpTransport):
        raise McpOAuthError("MCP OAuth requires streamable_http transport")
    _oauth_config(transport)
    return transport


def _oauth_config(transport: McpStreamableHttpTransport) -> McpOAuthConfig:
    if transport.oauth is None:
        raise McpOAuthError("MCP server does not have OAuth configured")
    return transport.oauth


async def _write_http_response(
    writer: asyncio.StreamWriter,
    status_code: int,
    body: str,
) -> None:
    reason = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
    }.get(status_code, "OK")
    encoded = body.encode("utf-8")
    writer.write(
        (
            f"HTTP/1.1 {status_code} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(encoded)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        + encoded
    )
    await writer.drain()


def _oauth_page(message: str) -> str:
    return f"<!doctype html><html><body><p>{html.escape(message)}</p></body></html>"


__all__ = [
    "McpOAuthError",
    "McpOAuthLoginRequiredError",
    "McpOAuthLoginResult",
    "McpOAuthTokenStorage",
    "build_mcp_oauth_http_client",
    "clear_mcp_oauth_credentials",
    "login_mcp_oauth_server",
    "mcp_oauth_config_fingerprint",
    "require_mcp_oauth_login",
    "wait_for_mcp_oauth_callback",
]
