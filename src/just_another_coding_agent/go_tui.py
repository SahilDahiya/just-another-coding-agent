from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
import sysconfig
from collections.abc import Mapping
from pathlib import Path

GO_TUI_BINARY = "jaca-go.exe" if os.name == "nt" else "jaca-go"
GO_TUI_BUILD_ENV = "JACA_BUILD_TUI"
PACKAGE_NAME = "just-another-coding-agent"


def default_backend_command(python_executable: str | None = None) -> list[str]:
    executable = sys.executable if python_executable is None else python_executable
    return [executable, "-m", "just_another_coding_agent"]


def go_tui_build_requested(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get(GO_TUI_BUILD_ENV, "")
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


def explicit_update_command_json(*, repo_root: Path | None = None) -> str | None:
    command = explicit_update_command(repo_root=repo_root)
    if command is None:
        return None
    return json.dumps(command)


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
    if repo_root is not None and shutil.which("go"):
        return ["go", "run", "./cmd/jaca"], repo_root
    binary = resolve_go_tui_binary()
    return [str(binary)], None
