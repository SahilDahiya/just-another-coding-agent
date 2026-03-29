from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from collections.abc import Mapping
from pathlib import Path

GO_TUI_BINARY = "jaca-go.exe" if os.name == "nt" else "jaca-go"
GO_TUI_BUILD_ENV = "JACA_BUILD_TUI"


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
    try:
        binary = resolve_go_tui_binary()
    except RuntimeError as binary_error:
        repo_root = find_go_tui_repo_root()
        if repo_root is not None and shutil.which("go"):
            return ["go", "run", "./cmd/jaca"], repo_root
        raise binary_error
    return [str(binary)], None
