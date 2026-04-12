from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_compaction_context_window_utilization_defaults_to_07() -> None:
    result = _run_constants_import(env_value=None)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.7"


def test_compaction_context_window_utilization_accepts_env_override() -> None:
    result = _run_constants_import(env_value="0.1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1"


def test_compaction_context_window_utilization_rejects_invalid_env_override() -> None:
    result = _run_constants_import(env_value="abc")

    assert result.returncode != 0
    assert (
        "Invalid JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION"
        in result.stderr
    )


def test_compaction_context_window_utilization_rejects_out_of_range_env_override(
) -> None:
    result = _run_constants_import(env_value="1.5")

    assert result.returncode != 0
    assert (
        "Invalid JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION"
        in result.stderr
    )


def _run_constants_import(*, env_value: str | None) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    pythonpath_entries = [str(repo_root / "src"), str(repo_root)]
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    if env_value is None:
        env.pop("JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION", None)
    else:
        env["JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION"] = env_value
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from just_another_coding_agent.runtime.compaction.constants "
                "import SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION; "
                "print(SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION)"
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
