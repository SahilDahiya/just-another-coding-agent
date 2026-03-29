import pytest

from just_another_coding_agent.runtime.env import env_flag, trace_mode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        (" random ", True),
    ],
)
def test_env_flag_parses_common_boolean_like_values(
    monkeypatch,
    value: str | None,
    expected: bool,
) -> None:
    if value is None:
        monkeypatch.delenv("JACA_TRACE", raising=False)
    else:
        monkeypatch.setenv("JACA_TRACE", value)

    assert env_flag("JACA_TRACE") is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "off"),
        ("", "off"),
        ("off", "off"),
        ("local", "local"),
        ("logfire", "logfire"),
        (" LOCAL ", "local"),
    ],
)
def test_trace_mode_parses_explicit_runtime_modes(
    monkeypatch,
    value: str | None,
    expected: str,
) -> None:
    if value is None:
        monkeypatch.delenv("JACA_TRACE_MODE", raising=False)
    else:
        monkeypatch.setenv("JACA_TRACE_MODE", value)

    assert trace_mode() == expected


def test_trace_mode_fails_fast_on_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "bogus")

    with pytest.raises(
        RuntimeError,
        match="JACA_TRACE_MODE must be one of: off, local, logfire",
    ):
        trace_mode()
