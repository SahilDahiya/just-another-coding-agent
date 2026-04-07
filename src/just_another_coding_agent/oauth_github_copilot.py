from __future__ import annotations

import asyncio
import math
import secrets
import time
from dataclasses import dataclass
from typing import Final

import httpx

from just_another_coding_agent.oauth_store import GitHubCopilotCredentials

_CLIENT_ID: Final[str] = "Iv1.b507a08c87ecfe98"
_COPILOT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}
_INITIAL_POLL_INTERVAL_MULTIPLIER: Final[float] = 1.2
_SLOW_DOWN_POLL_INTERVAL_MULTIPLIER: Final[float] = 1.4
_KNOWN_COPILOT_MODELS: Final[tuple[str, ...]] = (
    "gpt-5-mini",
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex-max",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
)


class GitHubCopilotOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubCopilotLoginFlow:
    flow_id: str
    domain: str
    device_code: str
    interval_seconds: int
    expires_in_seconds: int
    verification_uri: str
    user_code: str


@dataclass(frozen=True)
class GitHubCopilotLoginStart:
    flow_id: str
    auth_url: str
    instructions: str


def normalize_github_domain(input_value: str) -> str | None:
    trimmed = input_value.strip()
    if not trimmed:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(
            trimmed if "://" in trimmed else f"https://{trimmed}"
        )
    except ValueError:
        return None
    hostname = (parsed.hostname or "").strip().lower()
    return hostname or None


def start_github_copilot_login(
    *, enterprise_domain: str | None = None
) -> tuple[GitHubCopilotLoginFlow, GitHubCopilotLoginStart]:
    domain = enterprise_domain or "github.com"
    device = _start_device_flow(domain)
    flow_id = secrets.token_hex(16)
    flow = GitHubCopilotLoginFlow(
        flow_id=flow_id,
        domain=domain,
        device_code=device["device_code"],
        interval_seconds=device["interval"],
        expires_in_seconds=device["expires_in"],
        verification_uri=device["verification_uri"],
        user_code=device["user_code"],
    )
    instructions = f"Enter code: {flow.user_code}"
    return flow, GitHubCopilotLoginStart(
        flow_id=flow_id,
        auth_url=flow.verification_uri,
        instructions=instructions,
    )


async def wait_for_github_copilot_login(
    flow: GitHubCopilotLoginFlow,
) -> GitHubCopilotCredentials:
    github_access_token = await _poll_for_github_access_token(flow)
    credentials = await refresh_github_copilot_credentials(
        github_access_token,
        enterprise_domain=_enterprise_domain_or_none(flow.domain),
    )
    await _enable_all_known_models(
        credentials.access,
        enterprise_domain=credentials.enterprise_domain,
    )
    return credentials


async def refresh_github_copilot_credentials(
    refresh_token: str,
    *,
    enterprise_domain: str | None = None,
) -> GitHubCopilotCredentials:
    return await _refresh_github_copilot_credentials_async(
        *( _refresh_github_copilot_request_args(
            refresh_token,
            enterprise_domain=enterprise_domain,
        ) )
    )


def refresh_github_copilot_credentials_sync(
    refresh_token: str,
    *,
    enterprise_domain: str | None = None,
) -> GitHubCopilotCredentials:
    return _refresh_github_copilot_credentials_sync_impl(
        *_refresh_github_copilot_request_args(
            refresh_token,
            enterprise_domain=enterprise_domain,
        )
    )


def _refresh_github_copilot_request_args(
    refresh_token: str,
    *,
    enterprise_domain: str | None,
) -> tuple[str, dict[str, str], str, str | None]:
    domain = enterprise_domain or "github.com"
    token_url = f"https://api.{domain}/copilot_internal/v2/token"
    request_headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {refresh_token}",
        **_COPILOT_HEADERS,
    }
    return token_url, request_headers, refresh_token, enterprise_domain


async def _refresh_github_copilot_credentials_async(
    token_url: str,
    request_headers: dict[str, str],
    refresh_token: str,
    enterprise_domain: str | None,
) -> GitHubCopilotCredentials:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(token_url, headers=request_headers)
    return _parse_copilot_refresh_response(response, refresh_token, enterprise_domain)


def _refresh_github_copilot_credentials_sync_impl(
    token_url: str,
    request_headers: dict[str, str],
    refresh_token: str,
    enterprise_domain: str | None,
) -> GitHubCopilotCredentials:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(token_url, headers=request_headers)
    return _parse_copilot_refresh_response(response, refresh_token, enterprise_domain)


def _parse_copilot_refresh_response(
    response: httpx.Response,
    refresh_token: str,
    enterprise_domain: str | None,
) -> GitHubCopilotCredentials:
    if response.status_code >= 400:
        raise GitHubCopilotOAuthError(
            f"copilot token refresh failed: {response.status_code}"
        )
    payload = response.json()
    access = payload.get("token")
    expires_at = payload.get("expires_at")
    if not isinstance(access, str) or not isinstance(expires_at, int):
        raise GitHubCopilotOAuthError("invalid Copilot token response")
    return GitHubCopilotCredentials(
        refresh=refresh_token,
        access=access,
        expires=(expires_at * 1000) - (5 * 60 * 1000),
        enterprise_domain=enterprise_domain,
    )


def get_github_copilot_base_url(
    token: str,
    enterprise_domain: str | None = None,
) -> str:
    marker = "proxy-ep="
    for part in token.split(";"):
        if part.startswith(marker):
            proxy_host = part[len(marker) :]
            return "https://" + proxy_host.replace("proxy.", "api.", 1)
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return "https://api.individual.githubcopilot.com"


def _enterprise_domain_or_none(domain: str) -> str | None:
    return None if domain == "github.com" else domain


async def _enable_model(
    token: str,
    *,
    model_id: str,
    enterprise_domain: str | None = None,
) -> bool:
    base_url = get_github_copilot_base_url(token, enterprise_domain)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/models/{model_id}/policy",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                **_COPILOT_HEADERS,
                "openai-intent": "chat-policy",
                "x-interaction-type": "chat-policy",
            },
            json={"state": "enabled"},
        )
    return response.is_success


async def _enable_all_known_models(
    token: str,
    *,
    enterprise_domain: str | None = None,
) -> None:
    await asyncio.gather(
        *(
            _enable_model(
                token,
                model_id=model_id,
                enterprise_domain=enterprise_domain,
            )
            for model_id in _KNOWN_COPILOT_MODELS
        ),
        return_exceptions=True,
    )


def _urls(domain: str) -> dict[str, str]:
    return {
        "device_code_url": f"https://{domain}/login/device/code",
        "access_token_url": f"https://{domain}/login/oauth/access_token",
    }


def _start_device_flow(domain: str) -> dict[str, str | int]:
    urls = _urls(domain)
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            urls["device_code_url"],
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _COPILOT_HEADERS["User-Agent"],
            },
            data={
                "client_id": _CLIENT_ID,
                "scope": "read:user",
            },
        )
    if response.status_code >= 400:
        raise GitHubCopilotOAuthError(
            f"device flow failed: {response.status_code}"
        )
    payload = response.json()
    required = (
        "device_code",
        "user_code",
        "verification_uri",
        "interval",
        "expires_in",
    )
    if not all(key in payload for key in required):
        raise GitHubCopilotOAuthError("invalid device code response")
    return payload


async def _poll_for_github_access_token(
    flow: GitHubCopilotLoginFlow,
) -> str:
    urls = _urls(flow.domain)
    deadline = time.time() + float(flow.expires_in_seconds)
    interval_ms = max(1000, int(flow.interval_seconds * 1000))
    interval_multiplier = _INITIAL_POLL_INTERVAL_MULTIPLIER
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() < deadline:
            remaining_ms = max(0, int((deadline - time.time()) * 1000))
            wait_ms = min(
                int(math.ceil(interval_ms * interval_multiplier)),
                remaining_ms,
            )
            await _sleep_milliseconds(wait_ms)
            response = await client.post(
                urls["access_token_url"],
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": _COPILOT_HEADERS["User-Agent"],
                },
                data={
                    "client_id": _CLIENT_ID,
                    "device_code": flow.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            if response.status_code >= 400:
                raise GitHubCopilotOAuthError(
                    f"device token polling failed: {response.status_code}"
                )
            payload = response.json()
            access_token = payload.get("access_token")
            if isinstance(access_token, str):
                return access_token
            error = payload.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval_ms = max(
                    1000, int((payload.get("interval") or flow.interval_seconds) * 1000)
                )
                interval_multiplier = _SLOW_DOWN_POLL_INTERVAL_MULTIPLIER
                continue
            raise GitHubCopilotOAuthError(f"device flow failed: {error}")
    raise GitHubCopilotOAuthError("device flow timed out")


async def _sleep_milliseconds(milliseconds: int) -> None:
    await asyncio.sleep(max(0.0, milliseconds / 1000.0))


__all__ = [
    "GitHubCopilotLoginFlow",
    "GitHubCopilotLoginStart",
    "GitHubCopilotOAuthError",
    "get_github_copilot_base_url",
    "normalize_github_domain",
    "refresh_github_copilot_credentials",
    "refresh_github_copilot_credentials_sync",
    "start_github_copilot_login",
    "wait_for_github_copilot_login",
]
