from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import secrets
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from just_another_coding_agent.oauth_store import (
    OpenAICodexCredentials,
)

_CLIENT_ID: Final[str] = "app_EMoamEEZ73f0CkXaXp7hrann"
_AUTHORIZE_URL: Final[str] = "https://auth.openai.com/oauth/authorize"
_TOKEN_URL: Final[str] = "https://auth.openai.com/oauth/token"
_REDIRECT_URI: Final[str] = "http://localhost:1455/auth/callback"
_SCOPE: Final[str] = "openid profile email offline_access"
_JWT_CLAIM_PATH: Final[str] = "https://api.openai.com/auth"
_CALLBACK_HOST: Final[str] = "localhost"
_CALLBACK_PORT: Final[int] = 1455
_CALLBACK_PATH: Final[str] = "/auth/callback"
_EXPIRY_SAFETY_MARGIN_MS: Final[int] = 5 * 60 * 1000


class OpenAICodexOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICodexLoginStart:
    flow_id: str
    auth_url: str
    instructions: str


@dataclass(frozen=True)
class OpenAICodexLoginFlow:
    flow_id: str
    verifier: str
    state: str


def start_openai_codex_login() -> tuple[OpenAICodexLoginFlow, OpenAICodexLoginStart]:
    verifier = _create_pkce_verifier()
    challenge = _create_pkce_challenge(verifier)
    state = secrets.token_hex(16)
    flow_id = secrets.token_hex(16)
    auth_url = _build_authorize_url(
        verifier=verifier,
        challenge=challenge,
        state=state,
    )
    return OpenAICodexLoginFlow(
        flow_id=flow_id,
        verifier=verifier,
        state=state,
    ), OpenAICodexLoginStart(
        flow_id=flow_id,
        auth_url=auth_url,
        instructions=(
            "Complete login in your browser. If JACA does not finish "
            "automatically, paste the one-time code shown in the browser here."
        ),
    )


async def finish_openai_codex_login(
    flow: OpenAICodexLoginFlow,
    callback_or_code: str,
) -> OpenAICodexCredentials:
    code, state = _parse_authorization_input(callback_or_code)
    if not code:
        raise OpenAICodexOAuthError("missing authorization code")
    if state is not None and state != flow.state:
        raise OpenAICodexOAuthError("OAuth state mismatch")
    return await _exchange_authorization_code(code=code, verifier=flow.verifier)


async def refresh_openai_codex_credentials(
    credentials: OpenAICodexCredentials,
) -> OpenAICodexCredentials:
    request_kwargs = _refresh_token_request_kwargs(credentials.refresh)
    return await _refresh_openai_codex_credentials_async(request_kwargs)


def refresh_openai_codex_credentials_sync(
    credentials: OpenAICodexCredentials,
) -> OpenAICodexCredentials:
    request_kwargs = _refresh_token_request_kwargs(credentials.refresh)
    return _refresh_openai_codex_credentials_sync(request_kwargs)


def _refresh_token_request_kwargs(refresh_token: str) -> dict[str, object]:
    return {
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "content": urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLIENT_ID,
            }
        ),
    }


async def _refresh_openai_codex_credentials_async(
    request_kwargs: dict[str, object],
) -> OpenAICodexCredentials:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(_TOKEN_URL, **request_kwargs)
    return _parse_token_refresh_response(response)


def _refresh_openai_codex_credentials_sync(
    request_kwargs: dict[str, object],
) -> OpenAICodexCredentials:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(_TOKEN_URL, **request_kwargs)
    return _parse_token_refresh_response(response)


def _parse_token_refresh_response(
    response: httpx.Response,
) -> OpenAICodexCredentials:
    if response.status_code >= 400:
        raise OpenAICodexOAuthError(
            f"token refresh failed: {response.status_code}"
        )
    payload = response.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access, str)
        or not isinstance(refresh, str)
        or not isinstance(expires_in, int)
    ):
        raise OpenAICodexOAuthError("invalid token refresh response")
    account_id = _extract_account_id(access)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires=_expires_at(expires_in),
        account_id=account_id,
    )


async def wait_for_openai_codex_callback(
    flow: OpenAICodexLoginFlow,
    *,
    timeout_seconds: float = 300.0,
) -> OpenAICodexCredentials:
    result: asyncio.Future[OpenAICodexCredentials] = (
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
                    writer,
                    405,
                    _oauth_page("Method not allowed."),
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
            if state != flow.state:
                await _write_http_response(
                    writer,
                    400,
                    _oauth_page("OAuth state mismatch."),
                )
                return
            credentials = await _exchange_authorization_code(
                code=code,
                verifier=flow.verifier,
            )
            if not result.done():
                result.set_result(credentials)
            await _write_http_response(
                writer,
                200,
                _oauth_success_page(code),
            )
        except Exception as error:
            if not result.done():
                result.set_exception(error)
            try:
                await _write_http_response(
                    writer,
                    500,
                    _oauth_page("Login failed. Return to JACA."),
                )
            except Exception:
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        _handle_client,
        host=_CALLBACK_HOST,
        port=_CALLBACK_PORT,
    )
    try:
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    finally:
        server.close()
        asyncio.create_task(server.wait_closed())


def _build_authorize_url(*, verifier: str, challenge: str, state: str) -> str:
    del verifier
    params = urlencode(
        {
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "jaca",
        }
    )
    return f"{_AUTHORIZE_URL}?{params}"


def _create_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _create_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _parse_authorization_input(input_value: str) -> tuple[str | None, str | None]:
    value = input_value.strip()
    if not value:
        return None, None
    try:
        parsed = urlparse(value)
    except ValueError:
        return value, None
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        return code, state
    if "code=" in value:
        query = parse_qs(value)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        return code, state
    return value, None


async def _exchange_authorization_code(
    *,
    code: str,
    verifier: str,
) -> OpenAICodexCredentials:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": _CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": _REDIRECT_URI,
                }
            ),
        )
    if response.status_code >= 400:
        raise OpenAICodexOAuthError(
            f"token exchange failed: {response.status_code}"
        )
    payload = response.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access, str)
        or not isinstance(refresh, str)
        or not isinstance(expires_in, int)
    ):
        raise OpenAICodexOAuthError("invalid token exchange response")
    account_id = _extract_account_id(access)
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires=_expires_at(expires_in),
        account_id=account_id,
    )


def _expires_at(expires_in_seconds: int) -> int:
    return (
        int(time.time() * 1000)
        + (expires_in_seconds * 1000)
        - _EXPIRY_SAFETY_MARGIN_MS
    )


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
    return (
        "<!doctype html><html><body>"
        f"<p>{html.escape(message)}</p>"
        "</body></html>"
    )


def _oauth_success_page(code: str) -> str:
    escaped_code = html.escape(code, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JACA login complete</title>
  <style>
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: #0b1020;
      color: #e8edf5;
    }}
    main {{
      max-width: 42rem;
      margin: 4rem auto;
      padding: 0 1.25rem;
    }}
    h1 {{
      margin: 0 0 0.75rem;
      font-size: 1.75rem;
    }}
    p {{
      margin: 0 0 1rem;
      color: #b7c3d9;
      line-height: 1.5;
    }}
    .row {{
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin: 1.5rem 0 0.75rem;
    }}
    input {{
      flex: 1 1 20rem;
      min-width: 0;
      padding: 0.8rem 0.9rem;
      border: 1px solid #31405f;
      border-radius: 0.75rem;
      background: #11182c;
      color: #f7fafc;
      font-size: 1rem;
    }}
    button {{
      padding: 0.8rem 1rem;
      border: 0;
      border-radius: 0.75rem;
      background: #8be28b;
      color: #09210c;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .status {{
      min-height: 1.2rem;
      font-size: 0.95rem;
      color: #8be28b;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Login complete</h1>
    <p>JACA should finish automatically.</p>
    <p>If it does not, copy this one-time code and paste it into JACA.</p>
    <div class="row">
      <input id="oauth-code" readonly value="{escaped_code}">
      <button id="copy-button" type="button">Copy code</button>
    </div>
    <div class="status" id="copy-status"></div>
  </main>
  <script>
    const input = document.getElementById("oauth-code");
    const button = document.getElementById("copy-button");
    const status = document.getElementById("copy-status");

    function selectCode() {{
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
    }}

    async function copyCode() {{
      selectCode();
      try {{
        await navigator.clipboard.writeText(input.value);
        status.textContent = "Code copied.";
      }} catch (_error) {{
        status.textContent = "Code selected. Copy it with Ctrl+C or Cmd+C.";
      }}
    }}

    button.addEventListener("click", copyCode);
    selectCode();
  </script>
</body>
</html>"""


def _extract_account_id(access_token: str) -> str:
    parts = access_token.split(".")
    if len(parts) != 3:
        raise OpenAICodexOAuthError("invalid access token")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        token_payload = json.loads(decoded.decode("utf-8"))
    except Exception as error:
        raise OpenAICodexOAuthError("failed to decode access token") from error
    auth_claim = token_payload.get(_JWT_CLAIM_PATH)
    if not isinstance(auth_claim, dict):
        raise OpenAICodexOAuthError("missing OpenAI auth claim")
    account_id = auth_claim.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise OpenAICodexOAuthError("missing ChatGPT account id in token")
    return account_id


__all__ = [
    "OpenAICodexLoginStart",
    "OpenAICodexLoginFlow",
    "OpenAICodexOAuthError",
    "finish_openai_codex_login",
    "refresh_openai_codex_credentials",
    "refresh_openai_codex_credentials_sync",
    "start_openai_codex_login",
    "wait_for_openai_codex_callback",
]
