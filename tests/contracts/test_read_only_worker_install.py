from __future__ import annotations

import pytest

import just_another_coding_agent.install_repair as install_repair
from just_another_coding_agent.tools.read_only_worker import launcher


def test_read_only_worker_install_command_uses_repo_rebuild_in_repo_checkout(
    tmp_path,
) -> None:
    assert (
        launcher.read_only_worker_install_command(repo_root=tmp_path / "repo")
        == "uv sync --reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def test_read_only_worker_install_command_uses_uv_tool_repair(
    monkeypatch,
    tmp_path,
) -> None:
    scripts_dir = tmp_path / ".local" / "share" / "uv" / "tools" / "pkg" / "bin"
    scripts_dir.mkdir(parents=True)
    monkeypatch.setattr(
        install_repair.sysconfig, "get_path", lambda key: str(scripts_dir)
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "uv")

    assert (
        launcher.read_only_worker_install_command()
        == "uv tool upgrade just-another-coding-agent --reinstall"
    )


def test_resolve_read_only_worker_command_reports_explicit_recovery_step(
    tmp_path,
    monkeypatch,
) -> None:
    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    monkeypatch.setattr(launcher, "__file__", str(tmp_path / "outside" / "launcher.py"))
    monkeypatch.setattr(
        install_repair.sysconfig,
        "get_path",
        lambda key: str(scripts_dir),
    )
    monkeypatch.setattr(install_repair, "package_installer", lambda: "")

    with pytest.raises(
        RuntimeError,
        match="python -m pip install --force-reinstall just-another-coding-agent",
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
