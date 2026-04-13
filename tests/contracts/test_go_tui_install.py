from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import just_another_coding_agent.go_tui as go_tui
import just_another_coding_agent.install_repair as install_repair


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


def test_go_tui_install_command_uses_repo_rebuild_in_repo_checkout(tmp_path) -> None:
    assert (
        go_tui.go_tui_install_command(repo_root=tmp_path / "repo")
        == "JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def test_explicit_update_command_detects_uv_tool_install(monkeypatch, tmp_path) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")

    assert go_tui.explicit_update_command() == [
        "uv",
        "tool",
        "upgrade",
        "just-another-coding-agent",
    ]


def test_explicit_update_command_detects_pipx_install(monkeypatch, tmp_path) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "pipx" / "venvs" / "jaca" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "pip")

    assert go_tui.explicit_update_command() == [
        "pipx",
        "upgrade",
        "just-another-coding-agent",
    ]


def test_explicit_update_command_detects_generic_pip_install(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "pip")

    assert go_tui.explicit_update_command() == [
        "python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "just-another-coding-agent",
    ]


def test_explicit_update_command_is_disabled_in_repo_checkout(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")

    assert go_tui.explicit_update_command(repo_root=tmp_path / "repo") is None


def test_explicit_update_command_is_disabled_outside_uv_tool_layout(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")

    assert go_tui.explicit_update_command() is None


def test_go_tui_install_command_uses_uv_tool_repair_for_uv_tool_installs(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")

    assert (
        go_tui.go_tui_install_command()
        == "uv tool upgrade just-another-coding-agent --reinstall"
    )


def test_go_tui_install_command_uses_pipx_repair_for_pipx_installs(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "pipx" / "venvs" / "jaca" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "pip")

    assert go_tui.go_tui_install_command() == "pipx reinstall just-another-coding-agent"


def test_go_tui_install_command_falls_back_to_pip_force_reinstall(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "")

    assert (
        go_tui.go_tui_install_command()
        == "python -m pip install --force-reinstall just-another-coding-agent"
    )


def test_available_installed_update_returns_notice_for_newer_release(
    monkeypatch, tmp_path
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")
    monkeypatch.setattr(
        go_tui,
        "load_cached_release_version",
        lambda: go_tui.CachedReleaseVersion(
            latest_version="0.1.6",
            last_checked_at=datetime.now(UTC),
        ),
    )

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
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")
    monkeypatch.setattr(
        go_tui,
        "load_cached_release_version",
        lambda: go_tui.CachedReleaseVersion(
            latest_version="0.1.6",
            last_checked_at=datetime.now(UTC),
        ),
    )

    assert go_tui.available_installed_update(
        current_version="0.1.5",
        repo_root=tmp_path / "repo",
    ) is None


def test_cached_release_version_round_trip(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "version.json"
    checked_at = datetime(2026, 4, 12, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(go_tui, "update_cache_path", lambda: cache_path)

    go_tui.write_cached_release_version("0.1.6", checked_at=checked_at)

    assert go_tui.load_cached_release_version() == go_tui.CachedReleaseVersion(
        latest_version="0.1.6",
        last_checked_at=checked_at,
    )


def test_should_refresh_cached_release_version_after_interval() -> None:
    now = datetime(2026, 4, 12, 12, 0, tzinfo=UTC)
    recent = go_tui.CachedReleaseVersion(
        latest_version="0.1.6",
        last_checked_at=now - timedelta(hours=1),
    )
    stale = go_tui.CachedReleaseVersion(
        latest_version="0.1.6",
        last_checked_at=now - timedelta(hours=25),
    )

    assert go_tui.should_refresh_cached_release_version(None, now=now) is True
    assert go_tui.should_refresh_cached_release_version(recent, now=now) is False
    assert go_tui.should_refresh_cached_release_version(stale, now=now) is True


def test_refresh_cached_release_version_in_background_skips_recent_cache(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        go_tui,
        "explicit_update_command",
        lambda **_: ["uv", "tool", "upgrade", "just-another-coding-agent"],
    )
    monkeypatch.setattr(
        go_tui,
        "load_cached_release_version",
        lambda: go_tui.CachedReleaseVersion(
            latest_version="0.1.6",
            last_checked_at=datetime.now(UTC),
        ),
    )

    def fail_thread(*args, **kwargs):
        raise AssertionError("background refresh should not start for fresh cache")

    monkeypatch.setattr(go_tui.threading, "Thread", fail_thread)

    go_tui.refresh_cached_release_version_in_background()


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
    monkeypatch.setattr(go_tui, "__file__", str(tmp_path / "outside" / "go_tui.py"))
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "")

    with pytest.raises(
        RuntimeError,
        match="python -m pip install --force-reinstall just-another-coding-agent",
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
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )

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
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == ["go", "run", "./cmd/jaca"]
    assert cwd == repo_root


def test_resolve_go_tui_launch_falls_back_to_repo_local_go_run_when_binary_missing(
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
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == ["go", "run", "./cmd/jaca"]
    assert cwd == repo_root


def test_resolve_go_tui_launch_reports_missing_binary_without_go_in_repo_checkout(
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
    monkeypatch.setattr(go_tui.shutil, "which", lambda name: None)
    monkeypatch.setattr(go_tui, "go_tui_go_run_requested", lambda: False)

    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "JACA_BUILD_TUI=1 uv sync --reinstall-package "
            "just-another-coding-agent --extra dev --extra test"
        ),
    ):
        go_tui.resolve_go_tui_launch()


def test_resolve_go_tui_launch_uses_installed_binary_outside_repo(
    tmp_path,
    monkeypatch,
) -> None:
    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    binary = scripts_dir / go_tui.GO_TUI_BINARY
    binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(go_tui, "__file__", str(tmp_path / "outside" / "go_tui.py"))
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(go_tui.shutil, "which", lambda name: "/usr/bin/go")

    command, cwd = go_tui.resolve_go_tui_launch()

    assert command == [str(binary)]
    assert cwd is None
