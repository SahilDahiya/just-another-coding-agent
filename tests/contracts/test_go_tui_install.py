from __future__ import annotations

import pytest

import just_another_coding_agent.go_tui as go_tui


def test_go_tui_build_is_opt_in() -> None:
    assert go_tui.go_tui_build_requested({}) is False
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "1"}) is True
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "true"}) is True
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "0"}) is False


def test_go_tui_install_command_is_explicit() -> None:
    assert (
        go_tui.go_tui_install_command()
        == "JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def test_resolve_go_tui_binary_reports_explicit_recovery_step(
    tmp_path,
    monkeypatch,
) -> None:
    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))

    with pytest.raises(
        RuntimeError,
        match=(
            "JACA_BUILD_TUI=1 uv sync --reinstall-package "
            "just-another-coding-agent --extra dev --extra test"
        ),
    ):
        go_tui.resolve_go_tui_binary()

    expected = scripts_dir / go_tui.GO_TUI_BINARY
    try:
        go_tui.resolve_go_tui_binary()
    except RuntimeError as error:
        assert str(expected) in str(error)
    else:  # pragma: no cover
        raise AssertionError("resolve_go_tui_binary() unexpectedly succeeded")
