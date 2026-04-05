from __future__ import annotations

from pathlib import Path

import pytest

from just_another_coding_agent.auth import (
    SECRET_STORE_SERVICE,
    AuthStoreError,
    clear_provider_secret,
    get_local_secret_store_status,
    get_provider_auth_status,
    resolve_provider_secret,
    set_provider_secret,
)


class _FakeKeyringErrors:
    class PasswordDeleteError(Exception):
        pass


class _FakeKeyring:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self.errors = _FakeKeyringErrors

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._values:
            raise self.errors.PasswordDeleteError()
        del self._values[(service, username)]


def test_set_and_resolve_provider_secret_uses_keyring(monkeypatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: fake,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = set_provider_secret("openai", "test-key")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "keychain"
    assert status.env_key == "OPENAI_API_KEY"
    assert status.reason == "ok"
    assert fake.get_password(SECRET_STORE_SERVICE, "OPENAI_API_KEY") == "test-key"
    assert resolve_provider_secret("openai") == "test-key"


def test_get_provider_auth_status_prefers_environment(monkeypatch) -> None:
    fake = _FakeKeyring()
    fake.set_password(SECRET_STORE_SERVICE, "GOOGLE_API_KEY", "from-keychain")
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: fake,
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "from-env")

    status = get_provider_auth_status("google")

    assert status.provider == "google"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "env"
    assert status.env_key == "GOOGLE_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("google") == "from-env"


def test_clear_provider_secret_removes_keychain_value(monkeypatch) -> None:
    fake = _FakeKeyring()
    fake.set_password(SECRET_STORE_SERVICE, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: fake,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = clear_provider_secret("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "ANTHROPIC_API_KEY"
    assert status.reason == "missing_secret"
    assert fake.get_password(SECRET_STORE_SERVICE, "ANTHROPIC_API_KEY") is None


def test_set_provider_secret_rejects_blank(monkeypatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: fake,
    )

    with pytest.raises(ValueError, match="non-empty"):
        set_provider_secret("google", "   ")


def test_missing_keyring_backend_is_tolerated_for_optional_lookup(monkeypatch) -> None:
    class _FailingKeyringErrors:
        class KeyringError(Exception):
            pass

    class _FailingKeyring:
        errors = _FailingKeyringErrors

        def get_password(self, service: str, username: str) -> str | None:
            raise self.errors.KeyringError("no backend")

    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: _FailingKeyring(),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        Path("/tmp/pytest-jaca-no-secrets.json"),
    )
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    assert resolve_provider_secret("ollama", allow_missing_keychain=True) is None


def test_local_secret_store_status_reports_missing_backend(monkeypatch) -> None:
    class _FailingKeyringErrors:
        class KeyringError(Exception):
            pass

        class NoKeyringError(KeyringError):
            pass

    class _FailingBackend:
        priority = 0

    class _FailingKeyring:
        errors = _FailingKeyringErrors

        def get_keyring(self):
            return _FailingBackend()

    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: _FailingKeyring(),
    )

    status = get_local_secret_store_status()

    assert status.available is False
    assert status.message is not None
    assert status.file_store_path.endswith(".jaca/secrets.json")
    assert "No supported OS keychain backend is available" in status.message


def test_set_provider_secret_reports_missing_keyring_backend_actionably(
    monkeypatch,
) -> None:
    class _FailingKeyringErrors:
        class KeyringError(Exception):
            pass

        class NoKeyringError(KeyringError):
            pass

    class _FailingKeyring:
        errors = _FailingKeyringErrors

        def set_password(self, service: str, username: str, password: str) -> None:
            raise self.errors.NoKeyringError(
                "No recommended backend was available. Install one."
            )

    monkeypatch.setattr(
        "just_another_coding_agent.secret_store._load_keyring",
        lambda: _FailingKeyring(),
    )

    with pytest.raises(
        AuthStoreError,
        match="No supported OS keychain backend is available",
    ):
        set_provider_secret("openai", "test-key")


def test_set_provider_secret_can_use_file_store_explicitly(
    monkeypatch,
    tmp_path,
) -> None:
    secret_path = tmp_path / "secrets.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        secret_path,
    )

    status = set_provider_secret("google", "file-token", storage="file")

    assert status.provider == "google"
    assert status.configured is True
    assert status.secret_configured is True
    assert status.requires_secret is True
    assert status.source == "file"
    assert status.env_key == "GOOGLE_API_KEY"
    assert status.reason == "ok"
    assert resolve_provider_secret("google") == "file-token"
    assert secret_path.exists()


def test_clear_provider_secret_removes_file_store_value(
    monkeypatch,
    tmp_path,
) -> None:
    secret_path = tmp_path / "secrets.json"
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        secret_path,
    )
    set_provider_secret("openai", "file-token", storage="file")

    status = clear_provider_secret("openai")

    assert status.provider == "openai"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.reason == "missing_secret"
    assert resolve_provider_secret("openai", allow_missing_keychain=True) is None
    assert not secret_path.exists()


def test_get_provider_auth_status_marks_local_ollama_ready_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    status = get_provider_auth_status("ollama")

    assert status.provider == "ollama"
    assert status.configured is True
    assert status.secret_configured is False
    assert status.requires_secret is False
    assert status.source == "none"
    assert status.reason == "local_endpoint_no_secret_required"


def test_get_provider_auth_status_marks_hosted_ollama_missing_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")

    status = get_provider_auth_status("ollama")

    assert status.provider == "ollama"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.reason == "missing_secret"


def test_get_provider_auth_status_marks_local_openai_endpoint_ready_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")

    status = get_provider_auth_status("openai")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.secret_configured is False
    assert status.requires_secret is False
    assert status.source == "none"
    assert status.reason == "local_endpoint_no_secret_required"


def test_get_provider_auth_status_marks_openrouter_missing_without_secret(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.secret_store.SECRET_FILE_PATH",
        tmp_path / "secrets.json",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    status = get_provider_auth_status("openrouter")

    assert status.provider == "openrouter"
    assert status.configured is False
    assert status.secret_configured is False
    assert status.requires_secret is True
    assert status.source == "none"
    assert status.env_key == "OPENROUTER_API_KEY"
    assert status.reason == "missing_secret"
