from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

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


class GitHubCopilotCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access: str
    refresh: str
    expires: int
    enterprise_domain: str | None = None


class OAuthStoreData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_codex: OpenAICodexCredentials | None = None
    github_copilot: GitHubCopilotCredentials | None = None


def load_oauth_store() -> OAuthStoreData:
    if not OAUTH_FILE_PATH.exists():
        return OAuthStoreData()
    try:
        payload = json.loads(OAUTH_FILE_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise OAuthStoreError(f"failed to read OAuth store: {error}") from error
    except json.JSONDecodeError as error:
        raise OAuthStoreError(f"invalid OAuth store JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise OAuthStoreError("invalid OAuth store payload")
    normalized = {}
    if "openai-codex" in payload:
        normalized["openai_codex"] = payload["openai-codex"]
    if "github-copilot" in payload:
        normalized["github_copilot"] = payload["github-copilot"]
    try:
        return OAuthStoreData.model_validate(normalized)
    except Exception as error:  # pragma: no cover - defensive pydantic shape error
        raise OAuthStoreError(f"invalid OAuth store data: {error}") from error


def save_oauth_store(data: OAuthStoreData) -> None:
    OAUTH_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if data.openai_codex is not None:
        payload["openai-codex"] = data.openai_codex.model_dump()
    if data.github_copilot is not None:
        payload["github-copilot"] = data.github_copilot.model_dump()
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=OAUTH_FILE_PATH.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.chmod(0o600)
        temp_path.replace(OAUTH_FILE_PATH)
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


def get_github_copilot_credentials() -> GitHubCopilotCredentials | None:
    return load_oauth_store().github_copilot


def set_github_copilot_credentials(credentials: GitHubCopilotCredentials) -> None:
    current = load_oauth_store()
    save_oauth_store(current.model_copy(update={"github_copilot": credentials}))


def clear_github_copilot_credentials() -> None:
    current = load_oauth_store()
    save_oauth_store(current.model_copy(update={"github_copilot": None}))


__all__ = [
    "OAUTH_FILE_PATH",
    "OAuthStoreData",
    "OAuthStoreError",
    "GitHubCopilotCredentials",
    "OpenAICodexCredentials",
    "clear_github_copilot_credentials",
    "clear_openai_codex_credentials",
    "get_github_copilot_credentials",
    "get_openai_codex_credentials",
    "load_oauth_store",
    "save_oauth_store",
    "set_github_copilot_credentials",
    "set_openai_codex_credentials",
]
