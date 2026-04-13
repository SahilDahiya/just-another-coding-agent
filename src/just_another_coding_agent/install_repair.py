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


def explicit_update_command(*, repo_root: Path | None = None) -> list[str] | None:
    if repo_root is not None:
        return None

    scripts_dir = sysconfig.get_path("scripts")
    if not scripts_dir:
        return None

    if package_installer() != "uv":
        return None

    if not is_uv_tool_scripts_dir(Path(scripts_dir)):
        return None

    return ["uv", "tool", "upgrade", PACKAGE_NAME]


def repair_install_command(*, repo_root: Path | None, build_tui: bool) -> str:
    if repo_root is not None:
        return repo_sync_command(build_tui=build_tui)

    update_command = explicit_update_command(repo_root=repo_root)
    if update_command is not None:
        return " ".join([*update_command, "--reinstall"])

    return f"python -m pip install --force-reinstall {PACKAGE_NAME}"


__all__ = [
    "PACKAGE_NAME",
    "explicit_update_command",
    "find_repo_root",
    "is_uv_tool_scripts_dir",
    "package_installer",
    "repair_install_command",
    "repo_sync_command",
]
