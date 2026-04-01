from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .model_catalog import ProviderName

AuthSource = Literal["env", "keychain", "file", "none"]
AuthStorageKind = Literal["keychain", "file"]


class ProviderAuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    configured: bool
    source: AuthSource
    env_key: str


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
]
