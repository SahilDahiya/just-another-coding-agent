from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import smoke_uv_tool_install as smoke


def test_tool_scripts_dir_uses_scripts_on_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(smoke.os, "name", "nt", raising=False)

    assert smoke.tool_scripts_dir(tmp_path) == tmp_path / smoke.PACKAGE_NAME / "Scripts"


def test_tool_scripts_dir_uses_bin_on_posix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(smoke.os, "name", "posix", raising=False)

    assert smoke.tool_scripts_dir(tmp_path) == tmp_path / smoke.PACKAGE_NAME / "bin"


def test_probe_installed_binary_requires_existing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="installed bundled binary not found"):
        smoke.probe_installed_binary(
            path=tmp_path / "missing.exe",
            args=[],
            env={},
        )


def test_probe_installed_binary_surfaces_failure_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    binary = tmp_path / "jaca-go.exe"
    binary.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=216,
            stdout="stdout text",
            stderr="stderr text",
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        smoke.probe_installed_binary(
            path=binary,
            args=["-h"],
            env={},
        )

    assert excinfo.value.code == 216
    captured = capsys.readouterr()
    assert "bundled binary probe failed" in captured.err
    assert "stdout text" in captured.err
    assert "stderr text" in captured.err


def test_probe_installed_binary_accepts_success(monkeypatch, tmp_path: Path) -> None:
    binary = tmp_path / "jaca-go.exe"
    binary.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    smoke.probe_installed_binary(
        path=binary,
        args=["-h"],
        env={},
    )
