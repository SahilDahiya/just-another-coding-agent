from __future__ import annotations

import pytest

from just_another_coding_agent.auth import (
    SECRET_STORE_SERVICE,
    AuthStoreError,
    clear_provider_secret,
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
        "just_another_coding_agent.auth._load_keyring",
        lambda: fake,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = set_provider_secret("openai", "test-key")

    assert status.provider == "openai"
    assert status.configured is True
    assert status.source == "keychain"
    assert fake.get_password(SECRET_STORE_SERVICE, "OPENAI_API_KEY") == "test-key"
    assert resolve_provider_secret("openai") == "test-key"


def test_get_provider_auth_status_prefers_environment(monkeypatch) -> None:
    fake = _FakeKeyring()
    fake.set_password(SECRET_STORE_SERVICE, "GITHUB_API_KEY", "from-keychain")
    monkeypatch.setattr(
        "just_another_coding_agent.auth._load_keyring",
        lambda: fake,
    )
    monkeypatch.setenv("GITHUB_API_KEY", "from-env")

    status = get_provider_auth_status("github")

    assert status.provider == "github"
    assert status.configured is True
    assert status.source == "env"
    assert resolve_provider_secret("github") == "from-env"


def test_clear_provider_secret_removes_keychain_value(monkeypatch) -> None:
    fake = _FakeKeyring()
    fake.set_password(SECRET_STORE_SERVICE, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "just_another_coding_agent.auth._load_keyring",
        lambda: fake,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = clear_provider_secret("anthropic")

    assert status.provider == "anthropic"
    assert status.configured is False
    assert status.source == "none"
    assert fake.get_password(SECRET_STORE_SERVICE, "ANTHROPIC_API_KEY") is None


def test_set_provider_secret_rejects_blank(monkeypatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(
        "just_another_coding_agent.auth._load_keyring",
        lambda: fake,
    )

    with pytest.raises(ValueError, match="non-empty"):
        set_provider_secret("github", "   ")


def test_missing_keyring_backend_is_tolerated_for_optional_lookup(monkeypatch) -> None:
    class _FailingKeyringErrors:
        class KeyringError(Exception):
            pass

    class _FailingKeyring:
        errors = _FailingKeyringErrors

        def get_password(self, service: str, username: str) -> str | None:
            raise self.errors.KeyringError("no backend")

    monkeypatch.setattr(
        "just_another_coding_agent.auth._load_keyring",
        lambda: _FailingKeyring(),
    )
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    assert resolve_provider_secret("ollama", allow_missing_keychain=True) is None


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
        "just_another_coding_agent.auth._load_keyring",
        lambda: _FailingKeyring(),
    )

    with pytest.raises(
        AuthStoreError,
        match="No supported OS keychain backend is available",
    ):
        set_provider_secret("openai", "test-key")
