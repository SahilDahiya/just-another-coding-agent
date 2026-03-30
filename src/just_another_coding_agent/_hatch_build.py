from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from just_another_coding_agent.go_tui import GO_TUI_BINARY, go_tui_build_requested
from just_another_coding_agent.tools.read_only_worker.launcher import (
    READ_ONLY_WORKER_BINARY,
)


def _build_go_binary(
    *,
    project_root: Path,
    build_dir: Path,
    output_name: str,
    package_path: str,
) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_dir / output_name
    env = os.environ.copy()
    env.setdefault("CGO_ENABLED", "0")
    completed = subprocess.run(
        ["go", "build", "-o", str(output_path), package_path],
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
        raise RuntimeError(f"Go binary was not created: {output_path}")
    if os.name != "nt":
        output_path.chmod(output_path.stat().st_mode | 0o111)
    return output_path


class build_hook(BuildHookInterface):  # noqa: N801
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        del version
        shared_scripts = dict(build_data.get("shared_scripts", {}))

        helper_binary_path = _build_go_binary(
            project_root=Path(self.root),
            build_dir=Path(self.directory) / "read-only-worker",
            output_name=READ_ONLY_WORKER_BINARY,
            package_path="./cmd/jaca-read-only-worker",
        )
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        shared_scripts[str(helper_binary_path)] = helper_binary_path.name

        if go_tui_build_requested():
            tui_binary_path = _build_go_binary(
                project_root=Path(self.root),
                build_dir=Path(self.directory) / "go-tui",
                output_name=GO_TUI_BINARY,
                package_path="./cmd/jaca",
            )
            shared_scripts[str(tui_binary_path)] = tui_binary_path.name

        build_data["shared_scripts"] = shared_scripts
