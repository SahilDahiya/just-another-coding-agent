#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path

PACKAGE_NAME = "just-another-coding-agent"


def isolated_uv_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["UV_NO_CONFIG"] = "1"
    env["UV_CACHE_DIR"] = str(root / "uv-cache")
    env["HOME"] = str(home)
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    else:
        env["XDG_CACHE_HOME"] = str(home / ".cache")
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    return env


def tool_python_path(tool_dir: Path) -> Path:
    if os.name == "nt":
        return tool_dir / PACKAGE_NAME / "Scripts" / "python.exe"
    return tool_dir / PACKAGE_NAME / "bin" / "python"


def run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_uv_tool_install.py <wheel>")

    matches = [Path(path).resolve() for path in glob(sys.argv[1])]
    if not matches:
        candidate = Path(sys.argv[1]).resolve()
        if candidate.is_file():
            matches = [candidate]
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one wheel match for {sys.argv[1]!r}, got {len(matches)}"
        )
    wheel = matches[0]
    if not wheel.is_file():
        raise SystemExit(f"wheel not found: {wheel}")

    with tempfile.TemporaryDirectory(prefix="jaca-uv-tool-smoke-") as temp_dir:
        root = Path(temp_dir)
        env = isolated_uv_env(root)

        run(["uv", "tool", "install", "--force", str(wheel)], env=env)
        tool_dir = Path(run(["uv", "tool", "dir"], env=env).stdout.strip())
        python_path = tool_python_path(tool_dir)
        if not python_path.is_file():
            raise SystemExit(f"installed tool python not found: {python_path}")

        probe = """
from just_another_coding_agent.go_tui import explicit_update_command, package_version
from just_another_coding_agent.tools.read_only_worker.launcher import read_only_worker_install_command
assert package_version()
assert explicit_update_command(repo_root=None) == ["uv", "tool", "upgrade", "just-another-coding-agent"]
assert read_only_worker_install_command(repo_root=None) == "uv tool upgrade just-another-coding-agent --reinstall"
"""
        run([str(python_path), "-c", probe], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
