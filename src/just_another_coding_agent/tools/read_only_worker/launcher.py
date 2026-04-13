from __future__ import annotations

import os
import sysconfig
from pathlib import Path

from just_another_coding_agent.install_repair import (
    find_repo_root,
    repair_install_command,
)

READ_ONLY_WORKER_BINARY = (
    "jaca-read-only-worker.exe" if os.name == "nt" else "jaca-read-only-worker"
)


def read_only_worker_install_command(*, repo_root: Path | None = None) -> str:
    return repair_install_command(repo_root=repo_root, build_tui=False)


def resolve_read_only_worker_command() -> tuple[str, ...]:
    scripts_dir = sysconfig.get_path("scripts")
    if not scripts_dir:
        raise RuntimeError("Python scripts directory is unavailable")

    binary = Path(scripts_dir) / READ_ONLY_WORKER_BINARY
    if not binary.is_file():
        repo_root = find_repo_root(Path(__file__).resolve())
        raise RuntimeError(
            "Installed read-only worker binary is missing. Reinstall it with "
            f"`{read_only_worker_install_command(repo_root=repo_root)}`: {binary}"
        )

    return (str(binary),)


__all__ = [
    "READ_ONLY_WORKER_BINARY",
    "read_only_worker_install_command",
    "resolve_read_only_worker_command",
]
