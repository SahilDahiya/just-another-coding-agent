import pytest

from just_another_coding_agent.runtime.env import env_flag


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
