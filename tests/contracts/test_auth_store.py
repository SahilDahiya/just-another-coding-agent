from __future__ import annotations

from pathlib import Path

import pytest

from just_another_coding_agent.auth import (
    clear_provider_secret,
    get_local_secret_store_status,
    get_provider_auth_status,
    resolve_openai_codex_oauth_credentials,
    resolve_provider_secret,
    set_provider_secret,
)


def test_set_and_resolve_provider_secret_uses_auth_file(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = set_provider_secret("openai", "test-key")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "file"
    assert status.env_key == "OPENAI_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("openai") == "test-key"
    assert auth_path.exists()


def test_get_provider_auth_status_prefers_environment(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    set_provider_secret("openai", "from-file")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")

    status = get_provider_auth_status("openai")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "env"
    assert status.env_key == "OPENAI_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("openai") == "from-env"


def test_clear_provider_secret_removes_file_value(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_provider_secret("anthropic", "test-key")

    status = clear_provider_secret("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "ANTHROPIC_API_KEY"
    assert status.reason == "missing_secret"
    assert resolve_provider_secret("anthropic") is None
    assert not auth_path.exists()


def test_set_provider_secret_rejects_blank() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        set_provider_secret("openai", "   ")


def test_local_secret_store_status_reports_auth_file_path(
    monkeypatch, tmp_path
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    status = get_local_secret_store_status()

    assert status.available is True
    assert status.message is None
    assert Path(status.file_store_path) == auth_path


def test_set_provider_secret_only_accepts_file_storage(monkeypatch, tmp_path) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        auth_path,
    )

    with pytest.raises(ValueError, match="unknown auth storage"):
        set_provider_secret("openai", "test-key", storage="keychain")  # type: ignore[arg-type]


def test_get_provider_auth_status_requires_openai_secret_even_with_local_base_url(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        tmp_path / "auth.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")

    status = get_provider_auth_status("openai")

    assert status.provider == "openai"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.reason == "missing_secret"


def test_get_provider_auth_status_marks_anthropic_missing_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.AUTH_FILE_PATH",
        tmp_path / "auth.json",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = get_provider_auth_status("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "ANTHROPIC_API_KEY"
    assert status.reason == "missing_secret"


@pytest.mark.asyncio
async def test_resolve_openai_codex_oauth_credentials_prefers_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCESS_TOKEN", "env-access")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_REFRESH_TOKEN", "env-refresh")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_EXPIRES_AT", "4102444800000")
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_ACCOUNT_ID", "acct-env")
    monkeypatch.setattr(
        "just_another_coding_agent.auth.get_openai_codex_credentials",
        lambda: None,
    )

    credentials = await resolve_openai_codex_oauth_credentials()

    assert credentials is not None
    assert credentials.access == "env-access"
    assert credentials.refresh == "env-refresh"
    assert credentials.account_id == "acct-env"
