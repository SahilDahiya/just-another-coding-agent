import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluations"
    / "scripts"
    / "run_tb2_submission_slice.sh"
)
GIT_BASH_PATH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _copy_launcher(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "evaluations" / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher_path = scripts_dir / SCRIPT_PATH.name
    validator_src = (
        Path(__file__).resolve().parents[2]
        / "evaluations"
        / "scripts"
        / "validate_tb2_bundle.py"
    )
    validator_dst = scripts_dir / "validate_tb2_bundle.py"
    launcher_path.write_text(
        SCRIPT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    launcher_path.chmod(0o755)
    validator_dst.write_text(
        validator_src.read_text(encoding="utf-8").replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    validator_dst.chmod(0o755)
    real_scripts_dir = Path(__file__).resolve().parents[2] / "evaluations" / "scripts"
    for sh_file in real_scripts_dir.glob("*.sh"):
        if sh_file.name == SCRIPT_PATH.name:
            continue
        dst = scripts_dir / sh_file.name
        dst.write_text(
            sh_file.read_text(encoding="utf-8").replace("\r\n", "\n"),
            encoding="utf-8",
            newline="\n",
        )
        dst.chmod(0o755)
    return launcher_path


def _launcher_command(path: Path) -> list[str]:
    if os.name == "nt":
        return [_bash_executable(), _bash_path(path)]
    return [str(path)]


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


def _write_task_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fix-git\nregex-log\nlog-summary-date-ranges\n")


def _launcher_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    harbor_log = tmp_path / "harbor-args.log"
    task_file = tmp_path / "tasks" / "slice-a.txt"
    _write_task_file(task_file)

    _write_executable(
        bin_dir / "docker",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        bin_dir / "harbor",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf "%s\\0" "$@" >> "$HARBOR_LOG"\n'
            'printf "\\n" >> "$HARBOR_LOG"\n'
            'job_name=""\n'
            'jobs_dir=""\n'
            "tasks=()\n"
            'while (($#)); do\n'
            '  case "$1" in\n'
            '    --job-name)\n'
            '      job_name="$2"; shift 2;;\n'
            '    --jobs-dir)\n'
            '      jobs_dir="$2"; shift 2;;\n'
            '    --task-name)\n'
            '      tasks+=("$2"); shift 2;;\n'
            '    *)\n'
            '      shift;;\n'
            '  esac\n'
            'done\n'
            'if [[ -n "${job_name}" && -n "${jobs_dir}" ]]; then\n'
            '  mkdir -p "${jobs_dir}/${job_name}"\n'
            '  for task in "${tasks[@]}"; do\n'
            '    trial_dir="${jobs_dir}/${job_name}/${task}__stub"\n'
            '    mkdir -p "${trial_dir}"\n'
            '    TASK_NAME="$task" TRIAL_DIR="$trial_dir" \\\n'
            '      "$PYTHON_BIN" - <<'"'"'PY'"'"'\n'
            'import hashlib, json, os\n'
            'task = os.environ["TASK_NAME"]\n'
            'trial_dir = os.environ["TRIAL_DIR"]\n'
            'payload = {\n'
            '    "task_name": task,\n'
            '    "task_checksum": hashlib.sha256(task.encode()).hexdigest(),\n'
            '}\n'
            'with open(\n'
            '    os.path.join(trial_dir, "result.json"), "w", encoding="utf-8"\n'
            ') as fh:\n'
            '    json.dump(payload, fh)\n'
            'PY\n'
            '  done\n'
            'fi\n'
            'exit "${HARBOR_EXIT_CODE:-0}"\n'
        ),
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "SKIP_DOTENV": "1",
            "OLLAMA_API_KEY": "test-key",
            "MODEL": "ollama:glm-5:cloud",
            "JOBS_DIR": str(tmp_path / "jobs"),
            "SUBMISSION_ID": "submission-test",
            "TARGET_TRIALS": "5",
            "PASSES_PER_RUN": "1",
            "TASK_FILE": _bash_path(task_file),
            "HARBOR_LOG": str(harbor_log),
            "PYTHON_BIN": _bash_path(Path(sys.executable)),
        }
    )
    return env


def _parse_harbor_invocations(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [line.split("\0")[:-1] for line in log_path.read_text().splitlines() if line]


def test_slice_launcher_records_completed_first_pass_and_task_names(
    tmp_path: Path,
) -> None:
    launcher_path = _copy_launcher(tmp_path)
    env = _launcher_env(tmp_path)

    result = subprocess.run(
        _launcher_command(launcher_path),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0

    bundle_dir = (
        Path(env["JOBS_DIR"])
        / "submission-bundles"
        / env["SUBMISSION_ID"]
        / "slices"
        / "slice-a"
    )
    completed_jobs = (bundle_dir / "completed-jobs.txt").read_text().splitlines()
    invocations = _parse_harbor_invocations(Path(env["HARBOR_LOG"]))

    assert len(completed_jobs) == 1
    assert completed_jobs[0].startswith("submission-test-slice-a-pass-1-")
    assert invocations[0][invocations[0].index("--job-name") + 1].startswith(
        "submission-test-slice-a-pass-1-"
    )
    assert invocations[0][invocations[0].index("--n-attempts") + 1] == "1"
    assert invocations[0].count("--task-name") == 3
    task_name_indexes = [
        index for index, token in enumerate(invocations[0]) if token == "--task-name"
    ]
    assert invocations[0][task_name_indexes[0] + 1] == "fix-git"
    assert invocations[0][task_name_indexes[1] + 1] == "regex-log"


def test_slice_launcher_resumes_from_last_completed_pass(tmp_path: Path) -> None:
    launcher_path = _copy_launcher(tmp_path)
    env = _launcher_env(tmp_path)

    first_result = subprocess.run(
        _launcher_command(launcher_path),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    second_result = subprocess.run(
        _launcher_command(launcher_path),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert first_result.returncode == 0
    assert second_result.returncode == 0

    bundle_dir = (
        Path(env["JOBS_DIR"])
        / "submission-bundles"
        / env["SUBMISSION_ID"]
        / "slices"
        / "slice-a"
    )
    completed_jobs = (bundle_dir / "completed-jobs.txt").read_text().splitlines()
    assert len(completed_jobs) == 2
    assert any(
        job.startswith("submission-test-slice-a-pass-1-") for job in completed_jobs
    )
    assert any(
        job.startswith("submission-test-slice-a-pass-2-") for job in completed_jobs
    )


def test_slice_launcher_status_does_not_start_harbor(tmp_path: Path) -> None:
    launcher_path = _copy_launcher(tmp_path)
    env = _launcher_env(tmp_path)
    env["ACTION"] = "status"

    bundle_dir = (
        Path(env["JOBS_DIR"])
        / "submission-bundles"
        / env["SUBMISSION_ID"]
        / "slices"
        / "slice-a"
    )
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "slice-config.env").write_text(
        "\n".join(
            [
                "SLICE_MODEL=ollama:glm-5:cloud",
                "SLICE_THINKING=high",
                "SLICE_TARGET_TRIALS=5",
                "SLICE_DATASET=terminal-bench@2.0",
                f"SLICE_TASK_FILE={env['TASK_FILE']}",
                "",
            ]
        )
    )
    (bundle_dir / "completed-jobs.txt").write_text(
        "submission-test-slice-a-pass-1-20260327-000001\n"
        "submission-test-slice-a-pass-2-20260327-000002\n"
    )

    result = subprocess.run(
        _launcher_command(launcher_path),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "completed passes: 2/5" in result.stdout
    assert "task count: 3" in result.stdout
    assert _parse_harbor_invocations(Path(env["HARBOR_LOG"])) == []
