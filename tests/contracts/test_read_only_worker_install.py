from __future__ import annotations

import pytest

from just_another_coding_agent.tools.read_only_worker import launcher


def test_read_only_worker_install_command_is_explicit() -> None:
    assert (
        launcher.read_only_worker_install_command()
        == "uv sync --reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def test_resolve_read_only_worker_command_reports_explicit_recovery_step(
    tmp_path,
    monkeypatch,
) -> None:
    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    monkeypatch.setattr(
        launcher.sysconfig,
        "get_path",
        lambda key: str(scripts_dir),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "uv sync --reinstall-package just-another-coding-agent "
            "--extra dev --extra test"
        ),
    ):
        launcher.resolve_read_only_worker_command()

    expected = scripts_dir / launcher.READ_ONLY_WORKER_BINARY
    try:
        launcher.resolve_read_only_worker_command()
    except RuntimeError as error:
        assert str(expected) in str(error)
    else:  # pragma: no cover
        raise AssertionError(
            "resolve_read_only_worker_command() unexpectedly succeeded"
        )
