from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def build_go_binary(
    *,
    project_root: Path,
    build_dir: Path,
    output_name: str,
    package_path: str,
    prebuilt_env_var: str | None = None,
    failure_label: str = "Go binary",
) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_dir / output_name
    if prebuilt_env_var:
        prebuilt_path_value = os.environ.get(prebuilt_env_var)
        if prebuilt_path_value is not None:
            prebuilt_path = Path(prebuilt_path_value)
            if not prebuilt_path.is_file():
                raise RuntimeError(
                    f"{prebuilt_env_var} points to a missing file: {prebuilt_path}"
                )
            shutil.copy2(prebuilt_path, output_path)
            if os.name != "nt":
                output_path.chmod(output_path.stat().st_mode | 0o111)
            return output_path

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
        detail = (
            stderr or stdout or (f"go build exited with status {completed.returncode}")
        )
        raise RuntimeError(f"failed to build {failure_label}: {detail}")
    if not output_path.is_file():
        raise RuntimeError(f"{failure_label} was not created: {output_path}")
    if os.name != "nt":
        output_path.chmod(output_path.stat().st_mode | 0o111)
    return output_path
