import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "evaluations" / "scripts" / "tb2_glm5.sh"
)
GIT_BASH_PATH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _copy_harness(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "evaluations" / "scripts"
    scripts_dir.mkdir(parents=True)
    harness_path = scripts_dir / SCRIPT_PATH.name
    harness_path.write_text(
        SCRIPT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    harness_path.chmod(0o755)
    return harness_path


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


def _logged_script_path(path: Path) -> str:
    if os.name == "nt":
        return _bash_path(path)
    return str(path)


def _expected_log_line(
    script_path: Path,
    *,
    action: str,
    submission_id: str,
    task_file: str,
    passes: str,
    label: str,
) -> str:
    return (
        f"{_logged_script_path(script_path)}|action={action}|submission={submission_id}|"
        f"task={task_file}|passes={passes}|label={label}"
    )


def _write_stub_launcher(path: Path, label: str) -> None:
    _write_executable(
        path,
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf "%s|" "$0" >> "$HARNESS_LOG"\n'
            'printf "action=%s|" "${ACTION:-run}" >> "$HARNESS_LOG"\n'
            'printf "submission=%s|" "${SUBMISSION_ID:-}" >> "$HARNESS_LOG"\n'
            'printf "task=%s|" "${TASK_FILE:-}" >> "$HARNESS_LOG"\n'
            'printf "passes=%s|" "${PASSES_PER_RUN:-}" >> "$HARNESS_LOG"\n'
            f'printf "label={label}\\n" >> "$HARNESS_LOG"\n'
        ),
    )


def test_harness_run_full_delegates_to_full_launcher(tmp_path: Path) -> None:
    harness_path = _copy_harness(tmp_path)
    scripts_dir = harness_path.parent
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5.sh", "full")
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5_slice.sh", "slice")
    log_path = tmp_path / "harness.log"

    env = os.environ.copy()
    env["HARNESS_LOG"] = str(log_path)

    result = subprocess.run(
        _harness_command(harness_path, "run", "glm5-high"),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text().splitlines() == [
        _expected_log_line(
            scripts_dir / "run_tb2_submission_glm5.sh",
            action="run",
            submission_id="glm5-high",
            task_file="",
            passes="1",
            label="full",
        )
    ]


def test_harness_run_multiple_slices_delegates_to_slice_launcher(
    tmp_path: Path,
) -> None:
    harness_path = _copy_harness(tmp_path)
    scripts_dir = harness_path.parent
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5.sh", "full")
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5_slice.sh", "slice")
    log_path = tmp_path / "harness.log"
    task_a = tmp_path / "tasks" / "a.txt"
    task_b = tmp_path / "tasks" / "b.txt"
    task_a.parent.mkdir(parents=True)
    task_a.write_text("fix-git\n")
    task_b.write_text("regex-log\n")

    env = os.environ.copy()
    env["HARNESS_LOG"] = str(log_path)

    result = subprocess.run(
        [
            *_harness_command(harness_path),
            "run",
            "glm5-high",
            "--passes",
            "2",
            str(task_a),
            str(task_b),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text().splitlines() == [
        _expected_log_line(
            scripts_dir / "run_tb2_submission_glm5_slice.sh",
            action="run",
            submission_id="glm5-high",
            task_file=str(task_a),
            passes="2",
            label="slice",
        ),
        _expected_log_line(
            scripts_dir / "run_tb2_submission_glm5_slice.sh",
            action="run",
            submission_id="glm5-high",
            task_file=str(task_b),
            passes="2",
            label="slice",
        ),
    ]


def test_harness_status_slice_delegates_with_action_status(tmp_path: Path) -> None:
    harness_path = _copy_harness(tmp_path)
    scripts_dir = harness_path.parent
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5.sh", "full")
    _write_stub_launcher(scripts_dir / "run_tb2_submission_glm5_slice.sh", "slice")
    log_path = tmp_path / "harness.log"
    task_a = tmp_path / "tasks" / "a.txt"
    task_a.parent.mkdir(parents=True)
    task_a.write_text("fix-git\n")

    env = os.environ.copy()
    env["HARNESS_LOG"] = str(log_path)

    result = subprocess.run(
        _harness_command(harness_path, "status", "glm5-high", str(task_a)),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text().splitlines() == [
        _expected_log_line(
            scripts_dir / "run_tb2_submission_glm5_slice.sh",
            action="status",
            submission_id="glm5-high",
            task_file=str(task_a),
            passes="",
            label="slice",
        )
    ]
