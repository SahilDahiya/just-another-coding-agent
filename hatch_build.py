from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _go_tui_binary_name() -> str:
    return "jaca-go.exe" if os.name == "nt" else "jaca-go"


def _build_go_tui_binary(project_root: Path, build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_dir / _go_tui_binary_name()
    env = os.environ.copy()
    env.setdefault("CGO_ENABLED", "0")
    completed = subprocess.run(
        ["go", "build", "-o", str(output_path), "./cmd/jaca"],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or (
            f"go build exited with status {completed.returncode}"
        )
        raise RuntimeError(f"failed to build Go TUI binary: {detail}")
    if not output_path.is_file():
        raise RuntimeError(f"Go TUI binary was not created: {output_path}")
    if os.name != "nt":
        output_path.chmod(output_path.stat().st_mode | 0o111)
    return output_path


class build_hook(BuildHookInterface):  # noqa: N801
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        binary_path = _build_go_tui_binary(
            project_root=Path(self.root),
            build_dir=Path(self.directory) / "go-tui",
        )
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        shared_scripts = dict(build_data.get("shared_scripts", {}))
        shared_scripts[str(binary_path)] = binary_path.name
        build_data["shared_scripts"] = shared_scripts
