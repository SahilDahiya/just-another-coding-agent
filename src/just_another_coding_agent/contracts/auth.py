from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .model_catalog import ProviderName

AuthSource = Literal["env", "file", "none"]
AuthStorageKind = Literal["file"]
OAuthProviderName = Literal["openai-codex"]
McpServerAuthKind = Literal["none", "bearer_env", "oauth"]
McpServerTransportType = Literal["stdio", "streamable_http"]
McpServerAuthReason = Literal[
    "ok",
    "disabled",
    "no_auth_required",
    "missing_bearer_env",
    "oauth_login_required",
]
ProviderReadinessReason = Literal[
    "ok",
    "missing_secret",
    "local_endpoint_no_secret_required",
]


class ProviderAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    configured: bool
    secret_configured: bool
    requires_secret: bool
    source: AuthSource
    env_key: str
    reason: ProviderReadinessReason


class LocalSecretStoreStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    message: str | None = None
    file_store_path: str


class OAuthProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: OAuthProviderName
    logged_in: bool
    account_id: str | None = None
    expires_at: int | None = None


class McpServerAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str
    transport_type: McpServerTransportType
    enabled: bool
    auth_kind: McpServerAuthKind
    configured: bool
    reason: McpServerAuthReason
    env_var: str | None = None


__all__ = [
    "AuthSource",
    "AuthStorageKind",
    "LocalSecretStoreStatus",
    "McpServerAuthKind",
    "McpServerAuthReason",
    "McpServerAuthStatus",
    "McpServerTransportType",
    "OAuthProviderName",
    "OAuthProviderStatus",
    "ProviderAuthStatus",
    "ProviderReadinessReason",
]
