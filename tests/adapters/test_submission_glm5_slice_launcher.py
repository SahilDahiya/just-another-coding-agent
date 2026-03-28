import os
import shutil
import subprocess
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "run_tb2_submission_glm5_slice.sh"
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _copy_launcher(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher_path = scripts_dir / SCRIPT_PATH.name
    shutil.copy2(SCRIPT_PATH, launcher_path)
    return launcher_path


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
            'printf "%s\\0" "$@" >> "$HARBOR_LOG"\n'
            'printf "\\n" >> "$HARBOR_LOG"\n'
            'exit "${HARBOR_EXIT_CODE:-0}"\n'
        ),
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "SKIP_DOTENV": "1",
            "OLLAMA_API_KEY": "test-key",
            "MODEL": "ollama:glm-5:cloud",
            "JOBS_DIR": str(tmp_path / "jobs"),
            "SUBMISSION_ID": "glm5-test",
            "TARGET_TRIALS": "5",
            "PASSES_PER_RUN": "1",
            "TASK_FILE": str(task_file),
            "HARBOR_LOG": str(harbor_log),
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
        [str(launcher_path)],
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
    assert completed_jobs[0].startswith("glm5-test-slice-a-pass-1-")
    assert invocations[0][invocations[0].index("--job-name") + 1].startswith(
        "glm5-test-slice-a-pass-1-"
    )
    assert invocations[0][invocations[0].index("--n-attempts") + 1] == "1"
    assert invocations[0].count("--task-name") == 3
    assert invocations[0][invocations[0].index("--task-name") + 1] == "fix-git"
    assert invocations[0][invocations[0].index("--task-name", invocations[0].index("--task-name") + 1) + 1] == "regex-log"


def test_slice_launcher_resumes_from_last_completed_pass(tmp_path: Path) -> None:
    launcher_path = _copy_launcher(tmp_path)
    env = _launcher_env(tmp_path)

    first_result = subprocess.run(
        [str(launcher_path)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    second_result = subprocess.run(
        [str(launcher_path)],
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
    assert any(job.startswith("glm5-test-slice-a-pass-1-") for job in completed_jobs)
    assert any(job.startswith("glm5-test-slice-a-pass-2-") for job in completed_jobs)


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
        "glm5-test-slice-a-pass-1-20260327-000001\n"
        "glm5-test-slice-a-pass-2-20260327-000002\n"
    )

    result = subprocess.run(
        [str(launcher_path)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "completed passes: 2/5" in result.stdout
    assert "task count: 3" in result.stdout
    assert _parse_harbor_invocations(Path(env["HARBOR_LOG"])) == []
