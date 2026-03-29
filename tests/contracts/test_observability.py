from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from just_another_coding_agent.runtime import observability


def test_configure_observability_configures_logfire_when_trace_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setenv("JACA_TRACE", "1")
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    monkeypatch.setitem(
        sys.modules,
        "logfire",
        SimpleNamespace(configure=lambda **kwargs: calls.append(kwargs)),
    )
    monkeypatch.setattr(observability, "_configured", False)

    observability.configure_observability()

    assert calls == [
        {
            "send_to_logfire": True,
            "console": False,
            "service_name": "jaca",
        }
    ]


def test_configure_observability_fails_fast_without_logfire_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE", "1")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setattr(observability, "_configured", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(
        RuntimeError,
        match="JACA_TRACE=1 requires Logfire project credentials",
    ):
        observability.configure_observability()


def test_configure_observability_accepts_default_logfire_toml_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    logfire_dir = tmp_path / ".logfire"
    logfire_dir.mkdir()
    (logfire_dir / "default.toml").write_text(
        '\n'.join(
            [
                '[tokens."https://logfire-us.pydantic.dev"]',
                'token = "test-token"',
                'expiration = "2027-01-01T00:00:00Z"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("JACA_TRACE", "1")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "logfire",
        SimpleNamespace(configure=lambda **kwargs: calls.append(kwargs)),
    )
    monkeypatch.setattr(observability, "_configured", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    observability.configure_observability()

    assert calls == [
        {
            "send_to_logfire": True,
            "console": False,
            "service_name": "jaca",
        }
    ]
