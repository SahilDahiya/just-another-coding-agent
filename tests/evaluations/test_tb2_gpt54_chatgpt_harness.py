import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluations"
    / "scripts"
    / "tb2_gpt54_chatgpt.sh"
)
GIT_BASH_PATH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _copy_harness(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "evaluations" / "scripts"
    scripts_dir.mkdir(parents=True)
    harness_path = scripts_dir / SCRIPT_PATH.name
    delegated_path = scripts_dir / "tb2_glm5.sh"
    harness_path.write_text(
        SCRIPT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    harness_path.chmod(0o755)
    _write_executable(
        delegated_path,
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf "MODEL=%s\\n" "${MODEL:-}" >> "$HARNESS_LOG"\n'
            'printf "THINKING=%s\\n" "${JUST_ANOTHER_CODING_AGENT_THINKING:-}" >> "$HARNESS_LOG"\n'
            'printf "SUBMISSION_ID=%s\\n" "${SUBMISSION_ID:-}" >> "$HARNESS_LOG"\n'
            'printf "N_CONCURRENT=%s\\n" "${N_CONCURRENT:-}" >> "$HARNESS_LOG"\n'
            'printf "ARGS=%s\\n" "$*" >> "$HARNESS_LOG"\n'
        ),
    )
    return harness_path, delegated_path


def _harness_command(path: Path, *args: str) -> list[str]:
    if os.name == "nt":
        return [_bash_executable(), _bash_path(path), *args]
    return [str(path), *args]


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix()[2:]
    return f"/{drive}{tail}"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    if not GIT_BASH_PATH.exists():
        pytest.skip("Git Bash is required to execute shell launcher tests on Windows")
    return str(GIT_BASH_PATH)


def test_gpt54_chatgpt_harness_sets_submission_defaults(tmp_path: Path) -> None:
    harness_path, _ = _copy_harness(tmp_path)
    log_path = tmp_path / "harness.log"

    env = os.environ.copy()
    env["HARNESS_LOG"] = str(log_path)

    result = subprocess.run(
        _harness_command(harness_path, "run", "chatgpt-54", "--passes", "1", "tasks/b.txt"),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text().splitlines() == [
        "MODEL=openai-responses:gpt-5.4-chatgpt",
        "THINKING=high",
        "SUBMISSION_ID=gpt54-chatgpt-high",
        "N_CONCURRENT=5",
        "ARGS=run chatgpt-54 --passes 1 tasks/b.txt",
    ]


def test_gpt54_chatgpt_harness_allows_env_overrides(tmp_path: Path) -> None:
    harness_path, _ = _copy_harness(tmp_path)
    log_path = tmp_path / "harness.log"

    env = os.environ.copy()
    env.update(
        {
            "HARNESS_LOG": str(log_path),
            "MODEL": "openai-responses:gpt-5.4-mini-chatgpt",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "medium",
            "SUBMISSION_ID": "chatgpt-54-custom",
            "N_CONCURRENT": "1",
        }
    )

    result = subprocess.run(
        _harness_command(harness_path, "status", "chatgpt-54", "tasks/b.txt"),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text().splitlines() == [
        "MODEL=openai-responses:gpt-5.4-mini-chatgpt",
        "THINKING=medium",
        "SUBMISSION_ID=chatgpt-54-custom",
        "N_CONCURRENT=1",
        "ARGS=status chatgpt-54 tasks/b.txt",
    ]
