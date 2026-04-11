from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
import sysconfig
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

GO_TUI_BINARY = "jaca-go.exe" if os.name == "nt" else "jaca-go"
GO_TUI_BUILD_ENV = "JACA_BUILD_TUI"
GO_TUI_GO_RUN_ENV = "JACA_GO_RUN"
PACKAGE_NAME = "just-another-coding-agent"
UPDATE_CHECK_URL = "https://pypi.org/pypi/just-another-coding-agent/json"
UPDATE_CHECK_TIMEOUT = 5.0


@dataclass(frozen=True)
class AvailableUpdate:
    current_version: str
    latest_version: str
    command: tuple[str, ...]


def default_backend_command(python_executable: str | None = None) -> list[str]:
    executable = sys.executable if python_executable is None else python_executable
    return [executable, "-m", "just_another_coding_agent"]


def go_tui_build_requested(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get(GO_TUI_BUILD_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def go_tui_go_run_requested(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get(GO_TUI_GO_RUN_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def go_tui_install_command() -> str:
    return (
        f"{GO_TUI_BUILD_ENV}=1 uv sync "
        "--reinstall-package just-another-coding-agent "
        "--extra dev --extra test"
    )


def package_version() -> str:
    return importlib.metadata.version(PACKAGE_NAME)


def explicit_update_command(*, repo_root: Path | None = None) -> list[str] | None:
    if repo_root is not None:
        return None
    installer = _package_installer()
    scripts_dir = sysconfig.get_path("scripts")
    if installer != "uv" or not scripts_dir:
        return None
    if not _is_uv_tool_scripts_dir(Path(scripts_dir)):
        return None
    return ["uv", "tool", "upgrade", PACKAGE_NAME]


def available_installed_update(
    *,
    current_version: str,
    repo_root: Path | None = None,
) -> AvailableUpdate | None:
    command = explicit_update_command(repo_root=repo_root)
    if not current_version or command is None:
        return None

    latest_version = fetch_latest_release_version()
    if latest_version is None:
        return None

    newer, ok = is_newer_release_version(current_version, latest_version)
    if not ok or not newer:
        return None

    return AvailableUpdate(
        current_version=current_version,
        latest_version=latest_version,
        command=tuple(command),
    )


def fetch_latest_release_version() -> str | None:
    request = urllib.request.Request(UPDATE_CHECK_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=UPDATE_CHECK_TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    version = payload.get("info", {}).get("version", "")
    if not isinstance(version, str):
        return None
    return version.strip() or None


def is_newer_release_version(current: str, latest: str) -> tuple[bool, bool]:
    current_parts = parse_release_version(current)
    if current_parts is None:
        return False, False
    latest_parts = parse_release_version(latest)
    if latest_parts is None:
        return False, False
    if latest_parts > current_parts:
        return True, True
    return False, True


def parse_release_version(raw: str) -> tuple[int, int, int] | None:
    clean = raw.strip().removeprefix("v")
    if not clean or "-" in clean or "+" in clean:
        return None
    chunks = clean.split(".")
    if len(chunks) != 3:
        return None
    try:
        major, minor, patch = (int(chunk) for chunk in chunks)
    except ValueError:
        return None
    if major < 0 or minor < 0 or patch < 0:
        return None
    return major, minor, patch


def _package_installer() -> str:
    distribution = importlib.metadata.distribution(PACKAGE_NAME)
    installer = distribution.read_text("INSTALLER") or ""
    return installer.strip().lower()


def _is_uv_tool_scripts_dir(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    for index in range(len(parts) - 1):
        if parts[index] == "uv" and parts[index + 1] == "tools":
            return True
    return False


def resolve_go_tui_binary() -> Path:
    scripts_dir = sysconfig.get_path("scripts")
    if not scripts_dir:
        raise RuntimeError("Python scripts directory is unavailable")
    binary = Path(scripts_dir) / GO_TUI_BINARY
    if not binary.is_file():
        raise RuntimeError(
            "Installed Go TUI binary is missing. Build it explicitly with "
            f"`{go_tui_install_command()}`: {binary}"
        )
    return binary


def find_go_tui_repo_root(start: Path | None = None) -> Path | None:
    current = Path(__file__).resolve() if start is None else start.resolve()
    for candidate in (current, *current.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "go.mod").is_file()
            and (candidate / "cmd" / "jaca" / "main.go").is_file()
        ):
            return candidate
    return None


def resolve_go_tui_launch() -> tuple[list[str], Path | None]:
    repo_root = find_go_tui_repo_root()
    if repo_root is not None and go_tui_go_run_requested() and shutil.which("go"):
        return ["go", "run", "./cmd/jaca"], repo_root
    binary = resolve_go_tui_binary()
    return [str(binary)], None
