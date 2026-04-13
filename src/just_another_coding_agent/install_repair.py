from __future__ import annotations

import importlib.metadata
import sysconfig
from pathlib import Path

PACKAGE_NAME = "just-another-coding-agent"


def repo_sync_command(*, build_tui: bool) -> str:
    base = (
        f"uv sync --reinstall-package {PACKAGE_NAME} "
        "--extra dev --extra test"
    )
    if build_tui:
        return f"JACA_BUILD_TUI=1 {base}"
    return base


def find_repo_root(start: Path | None = None) -> Path | None:
    current = Path(__file__).resolve() if start is None else start.resolve()
    for candidate in (current, *current.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "go.mod").is_file()
            and (candidate / "cmd" / "jaca" / "main.go").is_file()
        ):
            return candidate
    return None


def package_installer() -> str:
    try:
        distribution = importlib.metadata.distribution(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return ""
    installer = distribution.read_text("INSTALLER") or ""
    return installer.strip().lower()


def is_uv_tool_scripts_dir(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    for index in range(len(parts) - 1):
        if parts[index] == "uv" and parts[index + 1] == "tools":
            return True
    return False


def is_pipx_scripts_dir(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return "pipx" in parts and "venvs" in parts


def explicit_update_command(*, repo_root: Path | None = None) -> list[str] | None:
    if repo_root is not None:
        return None

    scripts_dir = sysconfig.get_path("scripts")
    installer = package_installer()
    scripts_path = Path(scripts_dir) if scripts_dir else None

    if scripts_path is not None:
        if installer == "uv" and is_uv_tool_scripts_dir(scripts_path):
            return ["uv", "tool", "upgrade", PACKAGE_NAME]
        if is_pipx_scripts_dir(scripts_path):
            return ["pipx", "upgrade", PACKAGE_NAME]

    if installer == "pip":
        return ["python", "-m", "pip", "install", "--upgrade", PACKAGE_NAME]

    return None


def repair_install_command(*, repo_root: Path | None, build_tui: bool) -> str:
    if repo_root is not None:
        return repo_sync_command(build_tui=build_tui)

    scripts_dir = sysconfig.get_path("scripts")
    scripts_path = Path(scripts_dir) if scripts_dir else None
    installer = package_installer()

    if scripts_path is not None:
        if installer == "uv" and is_uv_tool_scripts_dir(scripts_path):
            return f"uv tool upgrade {PACKAGE_NAME} --reinstall"
        if is_pipx_scripts_dir(scripts_path):
            return f"pipx reinstall {PACKAGE_NAME}"

    return f"python -m pip install --force-reinstall {PACKAGE_NAME}"


__all__ = [
    "PACKAGE_NAME",
    "explicit_update_command",
    "find_repo_root",
    "is_pipx_scripts_dir",
    "is_uv_tool_scripts_dir",
    "package_installer",
    "repair_install_command",
    "repo_sync_command",
]
