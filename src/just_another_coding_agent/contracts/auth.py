from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .model_catalog import ProviderName

AuthSource = Literal["env", "keychain", "file", "none"]
AuthStorageKind = Literal["keychain", "file"]
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


__all__ = [
    "AuthSource",
    "AuthStorageKind",
    "LocalSecretStoreStatus",
    "ProviderAuthStatus",
    "ProviderReadinessReason",
]
