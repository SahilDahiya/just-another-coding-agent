from __future__ import annotations

import pytest

import just_another_coding_agent.go_tui as go_tui


def test_go_tui_build_is_opt_in() -> None:
    assert go_tui.go_tui_build_requested({}) is False
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "1"}) is True
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "true"}) is True
    assert go_tui.go_tui_build_requested({"JACA_BUILD_TUI": "0"}) is False


def test_go_tui_go_run_is_opt_in() -> None:
    assert go_tui.go_tui_go_run_requested({}) is False
    assert go_tui.go_tui_go_run_requested({"JACA_GO_RUN": "1"}) is True
    assert go_tui.go_tui_go_run_requested({"JACA_GO_RUN": "true"}) is True
    assert go_tui.go_tui_go_run_requested({"JACA_GO_RUN": "0"}) is False


def test_go_tui_install_command_is_explicit() -> None:
    assert (
        go_tui.go_tui_install_command()
        == "JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def test_explicit_update_command_detects_uv_tool_install(monkeypatch, tmp_path) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui, "_package_installer", lambda: "uv")

    assert go_tui.explicit_update_command() == [
        "uv",
        "tool",
        "upgrade",
        "just-another-coding-agent",
    ]


def test_explicit_update_command_is_disabled_in_repo_checkout(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui, "_package_installer", lambda: "uv")

    assert go_tui.explicit_update_command(repo_root=tmp_path / "repo") is None


def test_explicit_update_command_is_disabled_outside_uv_tool_layout(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui, "_package_installer", lambda: "uv")

    assert go_tui.explicit_update_command() is None


def test_available_installed_update_returns_notice_for_newer_release(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui, "_package_installer", lambda: "uv")
    monkeypatch.setattr(go_tui, "fetch_latest_release_version", lambda: "0.1.6")

    assert go_tui.available_installed_update(
        current_version="0.1.5"
    ) == go_tui.AvailableUpdate(
        current_version="0.1.5",
        latest_version="0.1.6",
        command=("uv", "tool", "upgrade", "just-another-coding-agent"),
    )


def test_available_installed_update_is_disabled_in_repo_checkout(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui, "_package_installer", lambda: "uv")
    monkeypatch.setattr(go_tui, "fetch_latest_release_version", lambda: "0.1.6")

    assert go_tui.available_installed_update(
        current_version="0.1.5",
        repo_root=tmp_path / "repo",
    ) is None


def test_is_newer_release_version_handles_equal_and_invalid_versions() -> None:
    assert go_tui.is_newer_release_version("0.1.0", "0.1.1") == (True, True)
    assert go_tui.is_newer_release_version("0.1.1", "0.1.1") == (False, True)
    assert go_tui.is_newer_release_version("dev", "0.1.1") == (False, False)


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


def test_find_go_tui_repo_root_detects_checkout_layout(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "src" / "just_another_coding_agent"
    package_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\n",
        encoding="utf-8",
    )
    (repo_root / "go.mod").write_text("module jaca\n", encoding="utf-8")
    (repo_root / "cmd" / "jaca").mkdir(parents=True)
    (repo_root / "cmd" / "jaca" / "main.go").write_text(
        "package main\n",
        encoding="utf-8",
    )

    assert go_tui.find_go_tui_repo_root(package_dir / "go_tui.py") == repo_root


def test_resolve_go_tui_launch_uses_installed_binary_by_default_in_repo_checkout(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "src" / "just_another_coding_agent"
    package_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\n",
        encoding="utf-8",
    )
    (repo_root / "go.mod").write_text("module jaca\n", encoding="utf-8")
    (repo_root / "cmd" / "jaca").mkdir(parents=True)
    (repo_root / "cmd" / "jaca" / "main.go").write_text(
        "package main\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(go_tui, "__file__", str(package_dir / "go_tui.py"))
    monkeypatch.setattr(
        go_tui.shutil,
        "which",
        lambda name: "/usr/bin/go" if name == "go" else None,
    )
    monkeypatch.setattr(go_tui, "go_tui_go_run_requested", lambda: False)

    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    binary = scripts_dir / go_tui.GO_TUI_BINARY
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == [str(binary)]
    assert cwd is None


def test_resolve_go_tui_launch_uses_repo_local_go_run_when_explicitly_requested(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "src" / "just_another_coding_agent"
    package_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\n",
        encoding="utf-8",
    )
    (repo_root / "go.mod").write_text("module jaca\n", encoding="utf-8")
    (repo_root / "cmd" / "jaca").mkdir(parents=True)
    (repo_root / "cmd" / "jaca" / "main.go").write_text(
        "package main\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(go_tui, "__file__", str(package_dir / "go_tui.py"))
    monkeypatch.setattr(
        go_tui.shutil,
        "which",
        lambda name: "/usr/bin/go" if name == "go" else None,
    )
    monkeypatch.setattr(go_tui, "go_tui_go_run_requested", lambda: True)

    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    (scripts_dir / go_tui.GO_TUI_BINARY).write_text("", encoding="utf-8")
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == ["go", "run", "./cmd/jaca"]
    assert cwd == repo_root


def test_resolve_go_tui_launch_uses_installed_binary_outside_repo(
    tmp_path,
    monkeypatch,
) -> None:
    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    binary = scripts_dir / go_tui.GO_TUI_BINARY
    binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(go_tui, "__file__", str(tmp_path / "outside" / "go_tui.py"))
    monkeypatch.setattr(go_tui.sysconfig, "get_path", lambda key: str(scripts_dir))
    monkeypatch.setattr(go_tui.shutil, "which", lambda name: "/usr/bin/go")

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == [str(binary)]
    assert cwd is None
