from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_default_shell_timeout_defaults_to_300_seconds() -> None:
    result = _run_shell_module_import(env_value=None)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "300"


def test_default_shell_timeout_accepts_env_override() -> None:
    result = _run_shell_module_import(env_value="600")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "600"


def test_default_shell_timeout_rejects_invalid_env_override() -> None:
    result = _run_shell_module_import(env_value="abc")

    assert result.returncode != 0
    assert "Invalid JACA_SHELL_TIMEOUT" in result.stderr


def test_default_shell_timeout_rejects_non_positive_env_override() -> None:
    result = _run_shell_module_import(env_value="0")

    assert result.returncode != 0
    assert "Invalid JACA_SHELL_TIMEOUT" in result.stderr


def _run_shell_module_import(
    *,
    env_value: str | None,
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    pythonpath_entries = [str(repo_root / "src"), str(repo_root)]
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    if env_value is None:
        env.pop("JACA_SHELL_TIMEOUT", None)
    else:
        env["JACA_SHELL_TIMEOUT"] = env_value
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from just_another_coding_agent.tools.shell import "
                "DEFAULT_SHELL_TIMEOUT_SECONDS; "
                "print(DEFAULT_SHELL_TIMEOUT_SECONDS)"
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
