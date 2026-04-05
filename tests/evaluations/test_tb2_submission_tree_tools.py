import json
import subprocess
from pathlib import Path

BUILD_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "evaluations"
    / "scripts"
    / "build_tb2_submission_tree.py"
)
VALIDATE_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "evaluations"
    / "scripts"
    / "validate_tb2_submission_tree.py"
)


def _write_result(trial_dir: Path, *, task_name: str, checksum: str) -> None:
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"task_name": task_name, "task_checksum": checksum}),
        encoding="utf-8",
    )
    (trial_dir / "artifact.txt").write_text("artifact\n", encoding="utf-8")
    (trial_dir / "config.json").write_text(
        json.dumps(
            {
                "timeout_multiplier": 1.0,
                "agent": {
                    "override_timeout_sec": None,
                    "max_timeout_sec": None,
                },
                "environment": {
                    "override_cpus": None,
                    "override_memory_mb": None,
                    "override_storage_mb": None,
                },
                "verifier": {
                    "override_timeout_sec": None,
                    "max_timeout_sec": None,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_job(job_dir: Path, task_specs: list[tuple[str, str]]) -> None:
    job_dir.mkdir(parents=True)
    for index, (task_name, checksum) in enumerate(task_specs, start=1):
        _write_result(
            job_dir / f"{task_name}__trial{index}",
            task_name=task_name,
            checksum=checksum,
        )


def test_build_submission_tree_from_slice_bundles(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    bundle_dir = jobs_dir / "submission-bundles" / "glm5-high"
    slice_a_dir = bundle_dir / "slices" / "a"
    slice_b_dir = bundle_dir / "slices" / "b"
    slice_a_dir.mkdir(parents=True)
    slice_b_dir.mkdir(parents=True)

    _write_job(jobs_dir / "job-a-1", [("task-a", "aaa")])
    _write_job(jobs_dir / "job-b-1", [("task-b", "bbb")])
    (slice_a_dir / "completed-jobs.txt").write_text("job-a-1\n", encoding="utf-8")
    (slice_b_dir / "completed-jobs.txt").write_text("job-b-1\n", encoding="utf-8")

    output_dir = tmp_path / "submission" / "terminal-bench" / "2.0" / "agent__model"
    result = subprocess.run(
        [
            "python3",
            str(BUILD_SCRIPT),
            str(output_dir),
            "--jobs-dir",
            str(jobs_dir),
            "--bundle-dir",
            str(bundle_dir),
            "--agent-url",
            "https://example.com/jaca",
            "--agent-display-name",
            "just-another-coding-agent",
            "--agent-org-display-name",
            "Sahil Dahiya",
            "--model-name",
            "glm-5",
            "--model-provider",
            "zhipu",
            "--model-display-name",
            "GLM 5",
            "--model-org-display-name",
            "Zhipu",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "metadata.yaml").exists()
    assert (output_dir / ".gitattributes").read_text(encoding="utf-8").strip() == (
        "*.jsonl filter=lfs diff=lfs merge=lfs -text"
    )
    assert (output_dir / "job-a-1").is_dir()
    assert (output_dir / "job-b-1").is_dir()


def test_validate_submission_tree_passes_for_complete_tree(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "agent_url: https://example.com/jaca",
                'agent_display_name: "just-another-coding-agent"',
                'agent_org_display_name: "Sahil Dahiya"',
                "models:",
                "  - model_name: glm-5",
                "    model_provider: zhipu",
                '    model_display_name: "GLM 5"',
                '    model_org_display_name: "Zhipu"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_job(submission_dir / "job-1", [("task-a", "aaa"), ("task-b", "bbb")])
    _write_job(submission_dir / "job-2", [("task-a", "aaa"), ("task-b", "bbb")])

    result = subprocess.run(
        [
            "python3",
            str(VALIDATE_SCRIPT),
            str(submission_dir),
            "--expected-unique-tasks",
            "2",
            "--min-trials-per-task",
            "2",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Unique tasks: 2" in result.stdout


def test_validate_submission_tree_fails_on_trial_count_gap(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "agent_url: https://example.com/jaca",
                'agent_display_name: "just-another-coding-agent"',
                'agent_org_display_name: "Sahil Dahiya"',
                "models:",
                "  - model_name: glm-5",
                "    model_provider: zhipu",
                '    model_display_name: "GLM 5"',
                '    model_org_display_name: "Zhipu"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_job(submission_dir / "job-1", [("task-a", "aaa")])

    result = subprocess.run(
        [
            "python3",
            str(VALIDATE_SCRIPT),
            str(submission_dir),
            "--expected-unique-tasks",
            "1",
            "--min-trials-per-task",
            "5",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Task aaa has only 1 trial(s), minimum 5 required" in result.stderr
