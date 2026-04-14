#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path

PACKAGE_NAME = "just-another-coding-agent"
WINDOWS_RIPGREP_BINARY = "rg.exe"
GO_TUI_BINARY = "jaca-go.exe" if os.name == "nt" else "jaca-go"


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


def tool_scripts_dir(tool_dir: Path) -> Path:
    if os.name == "nt":
        return tool_dir / PACKAGE_NAME / "Scripts"
    return tool_dir / PACKAGE_NAME / "bin"


def run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Capture output so the caller can read stdout (needed for `uv tool
    # dir`), but on failure surface both streams to stderr before raising
    # so CI logs show the actual error message instead of an opaque
    # CalledProcessError traceback.
    result = subprocess.run(
        cmd,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"command failed (exit {result.returncode}): {' '.join(cmd)}\n"
        )
        if result.stdout:
            sys.stderr.write(f"--- stdout ---\n{result.stdout}\n")
        if result.stderr:
            sys.stderr.write(f"--- stderr ---\n{result.stderr}\n")
        raise SystemExit(result.returncode)
    return result


def probe_installed_binary(
    *,
    path: Path,
    args: list[str],
    env: dict[str, str],
) -> None:
    if not path.is_file():
        raise SystemExit(f"installed bundled binary not found: {path}")
    result = subprocess.run(
        [str(path), *args],
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"bundled binary probe failed (exit {result.returncode}): {path}\n"
        )
        if result.stdout:
            sys.stderr.write(f"--- stdout ---\n{result.stdout}\n")
        if result.stderr:
            sys.stderr.write(f"--- stderr ---\n{result.stderr}\n")
        raise SystemExit(result.returncode)


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
        scripts_dir = tool_scripts_dir(tool_dir)
        if not python_path.is_file():
            raise SystemExit(f"installed tool python not found: {python_path}")

        probe = """
# `packaging` must be a direct dependency of jaca — go_tui imports
# packaging.version at module load time. Asserting its import explicitly
# keeps this end-to-end smoke test load-bearing for the Requires-Dist
# declaration, so the regression fixed in bd05ad6 can't silently come
# back via a transitive-dependency rearrangement upstream.
import packaging.version  # noqa: F401
import os
from just_another_coding_agent.go_tui import explicit_update_command, package_version
from just_another_coding_agent.tools.read_only_worker.launcher import (
    read_only_worker_install_command,
)
from just_another_coding_agent.tools.windows_search_tools import ensure_windows_search_tool
assert package_version()
assert explicit_update_command(repo_root=None) == [
    "uv",
    "tool",
    "upgrade",
    "just-another-coding-agent",
]
assert (
    read_only_worker_install_command(repo_root=None)
    == "uv tool upgrade just-another-coding-agent --reinstall"
)
if os.name == "nt":
    assert ensure_windows_search_tool("rg").lower().endswith("\\\\rg.exe")
"""
        run([str(python_path), "-c", probe], env=env)
        probe_installed_binary(
            path=scripts_dir / GO_TUI_BINARY,
            args=["-h"],
            env=env,
        )
        if os.name == "nt":
            bundled_rg = tool_dir / PACKAGE_NAME / "Scripts" / WINDOWS_RIPGREP_BINARY
            if not bundled_rg.is_file():
                raise SystemExit(f"bundled ripgrep not found: {bundled_rg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
