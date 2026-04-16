"""Unified public API for provider authentication.

Single entry point for all auth concerns: API-key resolution, OAuth
flows, secret-store access, and provider readiness.  Implementation
is split across backing modules:

- ``secret_store``: file-backed secret persistence
- ``oauth_openai_codex``: OpenAI Codex OAuth protocol
- ``oauth_store``: credential persistence for OAuth tokens
- ``provider_readiness``: readiness / status computation

Callers should import from this module, not from the backing modules
directly.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from just_another_coding_agent import secret_store
from just_another_coding_agent.contracts.auth import (
    AuthStorageKind,
    LocalSecretStoreStatus,
    OAuthProviderStatus,
    ProviderAuthStatus,
)
from just_another_coding_agent.contracts.model_catalog import ProviderName
from just_another_coding_agent.oauth_openai_codex import (
    OpenAICodexLoginFlow as _OpenAICodexLoginFlow,
)
from just_another_coding_agent.oauth_openai_codex import (
    finish_openai_codex_login,
    refresh_openai_codex_credentials,
    refresh_openai_codex_credentials_sync,
    start_openai_codex_login,
    wait_for_openai_codex_callback,
)
from just_another_coding_agent.oauth_store import (
    OpenAICodexCredentials,
    clear_openai_codex_credentials,
    get_openai_codex_credentials,
    set_openai_codex_credentials,
)
from just_another_coding_agent.provider_readiness import (
    compute_provider_readiness,
)

AuthStoreError = secret_store.AuthStoreError
PROVIDER_SECRET_ENV_KEYS = secret_store.PROVIDER_SECRET_ENV_KEYS
AUTH_FILE_PATH = secret_store.AUTH_FILE_PATH
OPENAI_CODEX_OAUTH_ACCESS_TOKEN_ENV = "OPENAI_CODEX_OAUTH_ACCESS_TOKEN"
OPENAI_CODEX_OAUTH_REFRESH_TOKEN_ENV = "OPENAI_CODEX_OAUTH_REFRESH_TOKEN"
OPENAI_CODEX_OAUTH_EXPIRES_AT_ENV = "OPENAI_CODEX_OAUTH_EXPIRES_AT"
OPENAI_CODEX_OAUTH_ACCOUNT_ID_ENV = "OPENAI_CODEX_OAUTH_ACCOUNT_ID"


class ProviderSecretValidationError(ValueError):
    pass


@dataclass(frozen=True)
class OpenAICodexLoginFlow:
    flow_id: str
    verifier: str
    state: str


@dataclass(frozen=True)
class ProviderSecretFileSetup:
    provider: ProviderName
    env_key: str
    file_path: str
    created: bool
    file_snippet: str
    entry_snippet: str


def resolve_provider_secret(
    provider: ProviderName,
) -> str | None:
    env_key = _provider_env_key(provider)
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value
    file_store_value = secret_store.get_file_store_secret(provider)
    if file_store_value:
        return file_store_value
    return None


def get_provider_auth_status(provider: ProviderName) -> ProviderAuthStatus:
    return compute_provider_readiness(provider)


def list_provider_auth_statuses() -> list[ProviderAuthStatus]:
    return [get_provider_auth_status(provider) for provider in PROVIDER_SECRET_ENV_KEYS]


def get_local_secret_store_status() -> LocalSecretStoreStatus:
    return LocalSecretStoreStatus(
        available=True,
        file_store_path=str(secret_store.AUTH_FILE_PATH),
    )


def prepare_provider_secret_file(provider: ProviderName) -> ProviderSecretFileSetup:
    env_key = _provider_env_key(provider)
    created = False
    if not secret_store.AUTH_FILE_PATH.exists():
        secret_store.save_file_store({})
        created = True
    else:
        secret_store.load_file_store()
    return ProviderSecretFileSetup(
        provider=provider,
        env_key=env_key,
        file_path=str(secret_store.AUTH_FILE_PATH),
        created=created,
        file_snippet=json.dumps({env_key: "..."}, indent=2, sort_keys=True),
        entry_snippet=f'"{env_key}": "..."',
    )


def get_oauth_provider_statuses() -> list[OAuthProviderStatus]:
    statuses: list[OAuthProviderStatus] = []

    openai_codex = get_openai_codex_credentials()
    if openai_codex is None:
        statuses.append(OAuthProviderStatus(provider="openai-codex", logged_in=False))
    else:
        statuses.append(
            OAuthProviderStatus(
                provider="openai-codex",
                logged_in=True,
                account_id=openai_codex.account_id,
                expires_at=openai_codex.expires,
            )
        )

    return statuses


def start_openai_codex_oauth_login() -> tuple[OpenAICodexLoginFlow, str, str, str]:
    flow, start = start_openai_codex_login()
    return (
        OpenAICodexLoginFlow(
            flow_id=flow.flow_id,
            verifier=flow.verifier,
            state=flow.state,
        ),
        start.flow_id,
        start.auth_url,
        start.instructions,
    )


async def complete_openai_codex_oauth_login(
    flow: OpenAICodexLoginFlow,
    callback_or_code: str,
) -> OAuthProviderStatus:
    credentials = await finish_openai_codex_login(
        _to_openai_codex_login_flow(flow),
        callback_or_code,
    )
    set_openai_codex_credentials(credentials)
    return OAuthProviderStatus(
        provider="openai-codex",
        logged_in=True,
        account_id=credentials.account_id,
        expires_at=credentials.expires,
    )


async def wait_for_openai_codex_oauth_login(
    flow: OpenAICodexLoginFlow,
) -> OAuthProviderStatus:
    credentials = await wait_for_openai_codex_callback(
        _to_openai_codex_login_flow(flow)
    )
    set_openai_codex_credentials(credentials)
    return OAuthProviderStatus(
        provider="openai-codex",
        logged_in=True,
        account_id=credentials.account_id,
        expires_at=credentials.expires,
    )


def clear_openai_codex_oauth_login() -> OAuthProviderStatus:
    clear_openai_codex_credentials()
    return OAuthProviderStatus(provider="openai-codex", logged_in=False)


async def resolve_openai_codex_oauth_credentials():
    env_credentials = _get_openai_codex_env_credentials()
    credentials = env_credentials or get_openai_codex_credentials()
    if credentials is None:
        return None
    if credentials.expires > int(time.time() * 1000):
        return credentials
    refreshed = await refresh_openai_codex_credentials(credentials)
    if env_credentials is None:
        set_openai_codex_credentials(refreshed)
    return refreshed


def resolve_openai_codex_oauth_credentials_sync():
    credentials = _get_openai_codex_env_credentials() or get_openai_codex_credentials()
    if credentials is None:
        return None
    if credentials.expires > int(time.time() * 1000):
        return credentials
    refreshed = refresh_openai_codex_credentials_sync(credentials)
    if _get_openai_codex_env_credentials() is None:
        set_openai_codex_credentials(refreshed)
    return refreshed


def set_provider_secret(
    provider: ProviderName,
    secret: str,
    *,
    storage: AuthStorageKind = "file",
) -> ProviderAuthStatus:
    trimmed = secret.strip()
    if not trimmed:
        raise ProviderSecretValidationError(
            "provider secret must be a non-empty string"
        )

    if storage != "file":  # pragma: no cover - guarded by typed callers
        raise ValueError(f"unknown auth storage: {storage}")
    secret_store.set_file_store_secret(provider, trimmed)
    return compute_provider_readiness(provider)


def clear_provider_secret(provider: ProviderName) -> ProviderAuthStatus:
    secret_store.clear_file_store_secret(provider)
    return compute_provider_readiness(provider)


def _provider_env_key(provider: ProviderName) -> str:
    return secret_store.provider_env_key(provider)


def _get_openai_codex_env_credentials() -> OpenAICodexCredentials | None:
    access = os.environ.get(OPENAI_CODEX_OAUTH_ACCESS_TOKEN_ENV, "").strip()
    refresh = os.environ.get(OPENAI_CODEX_OAUTH_REFRESH_TOKEN_ENV, "").strip()
    expires = os.environ.get(OPENAI_CODEX_OAUTH_EXPIRES_AT_ENV, "").strip()
    account_id = os.environ.get(OPENAI_CODEX_OAUTH_ACCOUNT_ID_ENV, "").strip()
    if not any((access, refresh, expires, account_id)):
        return None
    if not all((access, refresh, expires, account_id)):
        raise ValueError(
            "OpenAI Codex OAuth env credentials require access, refresh, "
            "expires, and account_id"
        )
    try:
        expires_at = int(expires)
    except ValueError as error:
        raise ValueError(
            "OpenAI Codex OAuth env credentials require integer expires"
        ) from error
    return OpenAICodexCredentials(
        access=access,
        refresh=refresh,
        expires=expires_at,
        account_id=account_id,
    )


def _to_openai_codex_login_flow(
    flow: OpenAICodexLoginFlow,
) -> _OpenAICodexLoginFlow:
    return _OpenAICodexLoginFlow(
        flow_id=flow.flow_id,
        verifier=flow.verifier,
        state=flow.state,
    )


__all__ = [
    "AuthStoreError",
    "AuthStorageKind",
    "LocalSecretStoreStatus",
    "OAuthProviderStatus",
    "OpenAICodexLoginFlow",
    "PROVIDER_SECRET_ENV_KEYS",
    "AUTH_FILE_PATH",
    "ProviderAuthStatus",
    "ProviderName",
    "ProviderSecretValidationError",
    "clear_provider_secret",
    "clear_openai_codex_oauth_login",
    "complete_openai_codex_oauth_login",
    "get_provider_auth_status",
    "get_local_secret_store_status",
    "get_oauth_provider_statuses",
    "list_provider_auth_statuses",
    "prepare_provider_secret_file",
    "resolve_openai_codex_oauth_credentials",
    "resolve_openai_codex_oauth_credentials_sync",
    "resolve_provider_secret",
    "set_provider_secret",
    "start_openai_codex_oauth_login",
    "wait_for_openai_codex_oauth_login",
]
