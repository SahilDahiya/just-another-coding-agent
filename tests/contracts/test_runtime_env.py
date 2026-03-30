import pytest

from just_another_coding_agent.runtime.env import trace_mode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "local"),
        ("", "local"),
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
