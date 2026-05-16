from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import BaseModel, ConfigDict

OAUTH_FILE_PATH = Path.home() / ".jaca" / "oauth.json"


class OAuthStoreError(RuntimeError):
    pass


class OpenAICodexCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access: str
    refresh: str
    expires: int
    account_id: str


class McpOAuthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str
    config_fingerprint: str
    tokens: dict[str, Any] | None = None
    client_info: dict[str, Any] | None = None


class OAuthStoreData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_codex: OpenAICodexCredentials | None = None
    mcp_servers: dict[str, McpOAuthRecord] = {}


def _oauth_file_path() -> Path:
    return Path.home() / ".jaca" / "oauth.json"


def load_oauth_store() -> OAuthStoreData:
    oauth_file_path = _oauth_file_path()
    if not oauth_file_path.exists():
        return OAuthStoreData()
    try:
        payload = json.loads(oauth_file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise OAuthStoreError(f"failed to read OAuth store: {error}") from error
    except json.JSONDecodeError as error:
        raise OAuthStoreError(f"invalid OAuth store JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise OAuthStoreError("invalid OAuth store payload")
    normalized = {}
    if "openai-codex" in payload:
        normalized["openai_codex"] = payload["openai-codex"]
    if "mcp_servers" in payload:
        normalized["mcp_servers"] = payload["mcp_servers"]
    try:
        data = OAuthStoreData.model_validate(normalized)
    except Exception as error:  # pragma: no cover - defensive pydantic shape error
        raise OAuthStoreError(f"invalid OAuth store data: {error}") from error
    for key, record in data.mcp_servers.items():
        expected_key = _mcp_oauth_record_key(
            server_id=record.server_id,
            config_fingerprint=record.config_fingerprint,
        )
        if key != expected_key:
            raise OAuthStoreError("invalid MCP OAuth store key")
        if record.tokens is not None:
            OAuthToken.model_validate(record.tokens)
        if record.client_info is not None:
            OAuthClientInformationFull.model_validate(record.client_info)
    return data


def save_oauth_store(data: OAuthStoreData) -> None:
    oauth_file_path = _oauth_file_path()
    oauth_file_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if data.openai_codex is not None:
        payload["openai-codex"] = data.openai_codex.model_dump()
    if data.mcp_servers:
        payload["mcp_servers"] = {
            key: record.model_dump(mode="json", exclude_none=True)
            for key, record in data.mcp_servers.items()
        }
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=oauth_file_path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.chmod(0o600)
        temp_path.replace(oauth_file_path)
    except OSError as error:
        raise OAuthStoreError(f"failed to write OAuth store: {error}") from error


def get_openai_codex_credentials() -> OpenAICodexCredentials | None:
    return load_oauth_store().openai_codex


def set_openai_codex_credentials(credentials: OpenAICodexCredentials) -> None:
    current = load_oauth_store()
    save_oauth_store(current.model_copy(update={"openai_codex": credentials}))


def clear_openai_codex_credentials() -> None:
    current = load_oauth_store()
    save_oauth_store(current.model_copy(update={"openai_codex": None}))


def get_mcp_oauth_record(
    *,
    server_id: str,
    config_fingerprint: str,
    store: OAuthStoreData | None = None,
) -> McpOAuthRecord | None:
    current = load_oauth_store() if store is None else store
    return current.mcp_servers.get(
        _mcp_oauth_record_key(
            server_id=server_id,
            config_fingerprint=config_fingerprint,
        )
    )


def set_mcp_oauth_record(record: McpOAuthRecord) -> None:
    if record.tokens is not None:
        OAuthToken.model_validate(record.tokens)
    if record.client_info is not None:
        OAuthClientInformationFull.model_validate(record.client_info)
    current = load_oauth_store()
    updated_records = dict(current.mcp_servers)
    updated_records[
        _mcp_oauth_record_key(
            server_id=record.server_id,
            config_fingerprint=record.config_fingerprint,
        )
    ] = record
    save_oauth_store(current.model_copy(update={"mcp_servers": updated_records}))


def clear_mcp_oauth_record(*, server_id: str, config_fingerprint: str) -> None:
    current = load_oauth_store()
    updated_records = dict(current.mcp_servers)
    updated_records.pop(
        _mcp_oauth_record_key(
            server_id=server_id,
            config_fingerprint=config_fingerprint,
        ),
        None,
    )
    save_oauth_store(current.model_copy(update={"mcp_servers": updated_records}))


def _mcp_oauth_record_key(*, server_id: str, config_fingerprint: str) -> str:
    return f"{server_id}:{config_fingerprint}"


__all__ = [
    "McpOAuthRecord",
    "OAUTH_FILE_PATH",
    "OAuthStoreData",
    "OAuthStoreError",
    "OpenAICodexCredentials",
    "clear_openai_codex_credentials",
    "clear_mcp_oauth_record",
    "get_openai_codex_credentials",
    "get_mcp_oauth_record",
    "load_oauth_store",
    "save_oauth_store",
    "set_openai_codex_credentials",
    "set_mcp_oauth_record",
]
