from __future__ import annotations

import os
from io import StringIO

import pytest

from just_another_coding_agent.tools import windows_search_tools as tools


def test_build_tool_process_env_prepends_managed_bin_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tools, "jaca_managed_bin_dir", lambda: tmp_path / "bin")

    env = tools.build_tool_process_env({"PATH": r"C:\Windows\System32"})

    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")


def test_ensure_windows_search_tool_downloads_missing_binary(
    monkeypatch,
    tmp_path,
) -> None:
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(tools, "jaca_managed_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(tools.os, "name", "nt")
    monkeypatch.setattr(tools.shutil, "which", lambda *args, **kwargs: None)

    def fake_download(spec):
        assert spec.tool_name == "rg"
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / "rg.exe"
        target.write_text("binary", encoding="utf-8")
        return str(target)

    monkeypatch.setattr(tools, "_download_and_install_windows_tool", fake_download)
    writer = StringIO()

    path = tools.ensure_windows_search_tool("rg", writer=writer)

    assert path == str(bin_dir / "rg.exe")
    assert writer.getvalue().splitlines() == [
        "ripgrep not found. Downloading...",
        f"ripgrep installed to {bin_dir / 'rg.exe'}",
    ]


def test_ensure_windows_search_tool_fails_hard_when_download_fails(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(tools, "jaca_managed_bin_dir", lambda: tmp_path / "bin")
    monkeypatch.setattr(tools.os, "name", "nt")
    monkeypatch.setattr(tools.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools,
        "_download_and_install_windows_tool",
        lambda spec: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    with pytest.raises(
        RuntimeError,
        match="Failed to install ripgrep \\(rg\\): network down",
    ):
        tools.ensure_windows_search_tool("rg", silent=True)
