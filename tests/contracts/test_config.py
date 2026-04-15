from __future__ import annotations

import os

from just_another_coding_agent.__main__ import _build_subprocess_env
from just_another_coding_agent.config import apply_trace_mode_to_env


def test_apply_trace_mode_to_env_preserves_explicit_env_when_config_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")

    apply_trace_mode_to_env({})

    assert os.environ["JACA_TRACE_MODE"] == "logfire"


def test_apply_trace_mode_to_env_uses_config_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("JACA_TRACE_MODE", raising=False)

    apply_trace_mode_to_env({"trace_mode": "logfire"})

    assert os.environ["JACA_TRACE_MODE"] == "logfire"


def test_build_subprocess_env_preserves_explicit_trace_mode(monkeypatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")

    env = _build_subprocess_env({})

    assert env["JACA_TRACE_MODE"] == "logfire"


def test_build_subprocess_env_uses_config_trace_mode_when_env_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JACA_TRACE_MODE", raising=False)

    env = _build_subprocess_env({"trace_mode": "logfire"})

    assert env["JACA_TRACE_MODE"] == "logfire"
