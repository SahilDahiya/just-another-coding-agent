#!/usr/bin/env python3
"""Validate a final Terminal Bench submission tree before PR upload.

read_when: checking the exact local submission tree before opening a PR
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

REQUIRED_METADATA_SCALARS = (
    "agent_url",
    "agent_display_name",
    "agent_org_display_name",
)
REQUIRED_MODEL_FIELDS = (
    "model_name",
    "model_provider",
    "model_display_name",
    "model_org_display_name",
)


def _validate_metadata(metadata_path: Path) -> None:
    if not metadata_path.exists():
        raise SystemExit(f"Missing metadata file: {metadata_path}")

    text = metadata_path.read_text(encoding="utf-8")
    for field in REQUIRED_METADATA_SCALARS:
        if f"{field}:" not in text:
            raise SystemExit(f"metadata.yaml missing required field: {field}")
    if "models:" not in text:
        raise SystemExit("metadata.yaml missing required models list")
    for field in REQUIRED_MODEL_FIELDS:
        if f"{field}:" not in text:
            raise SystemExit(f"metadata.yaml missing required model field: {field}")


def _job_dirs(submission_dir: Path) -> list[Path]:
    job_dirs = [
        path
        for path in sorted(submission_dir.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]
    if not job_dirs:
        raise SystemExit(f"No job directories found in {submission_dir}")
    return job_dirs


def _validate_config(config_path: Path) -> None:
    if not config_path.exists():
        raise SystemExit(f"Missing job config.json: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))

    if data.get("timeout_multiplier") != 1.0:
        raise SystemExit(f"{config_path} has timeout_multiplier != 1.0")

    agent = data.get("agent", {})
    environment = data.get("environment", {})
    verifier = data.get("verifier", {})

    forbidden = {
        "agent.override_timeout_sec": agent.get("override_timeout_sec"),
        "agent.max_timeout_sec": agent.get("max_timeout_sec"),
        "verifier.override_timeout_sec": verifier.get("override_timeout_sec"),
        "verifier.max_timeout_sec": verifier.get("max_timeout_sec"),
        "environment.override_cpus": environment.get("override_cpus"),
        "environment.override_memory_mb": environment.get("override_memory_mb"),
        "environment.override_storage_mb": environment.get("override_storage_mb"),
    }
    bad = [name for name, value in forbidden.items() if value is not None]
    if bad:
        raise SystemExit(
            f"{config_path} contains forbidden overrides: {', '.join(bad)}"
        )


def _load_trial_result(trial_dir: Path) -> tuple[str, str]:
    result_path = trial_dir / "result.json"
    if not result_path.exists():
        raise SystemExit(f"Missing trial result.json: {result_path}")
    data = json.loads(result_path.read_text(encoding="utf-8"))
    task_name = data.get("task_name")
    task_checksum = data.get("task_checksum")
    if not isinstance(task_name, str) or not isinstance(task_checksum, str):
        raise SystemExit(
            f"Invalid result payload in {result_path}: missing task_name/task_checksum."
        )
    artifact_paths = [
        path for path in trial_dir.iterdir() if path.name != "result.json"
    ]
    if not artifact_paths:
        raise SystemExit(f"Trial dir has no additional artifacts: {trial_dir}")
    return task_name, task_checksum


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a final Terminal Bench submission tree."
    )
    parser.add_argument("submission_dir")
    parser.add_argument("--expected-unique-tasks", type=int, default=None)
    parser.add_argument("--min-trials-per-task", type=int, default=5)
    args = parser.parse_args()

    submission_dir = Path(args.submission_dir)
    if not submission_dir.is_dir():
        raise SystemExit(f"Submission dir does not exist: {submission_dir}")

    _validate_metadata(submission_dir / "metadata.yaml")
    job_dirs = _job_dirs(submission_dir)

    task_name_to_checksum: dict[str, str] = {}
    task_counts: Counter[str] = Counter()

    for job_dir in job_dirs:
        trial_dirs = [path for path in sorted(job_dir.iterdir()) if path.is_dir()]
        if not trial_dirs:
            raise SystemExit(f"Job dir has no trial directories: {job_dir}")

        first_trial_config = trial_dirs[0] / "config.json"
        _validate_config(first_trial_config)

        for trial_dir in trial_dirs:
            task_name, task_checksum = _load_trial_result(trial_dir)
            previous = task_name_to_checksum.get(task_name)
            if previous is not None and previous != task_checksum:
                raise SystemExit(
                    f"Task {task_name} has checksum drift in submission tree: "
                    f"{previous} != {task_checksum}"
                )
            task_name_to_checksum[task_name] = task_checksum
            task_counts[task_checksum] += 1

    if (
        args.expected_unique_tasks is not None
        and len(task_counts) != args.expected_unique_tasks
    ):
        raise SystemExit(
            f"Submission covers {len(task_counts)} unique task(s), "
            f"expected {args.expected_unique_tasks}"
        )

    bad_counts = [
        (checksum, count)
        for checksum, count in sorted(task_counts.items())
        if count < args.min_trials_per_task
    ]
    if bad_counts:
        details = "\n".join(
            "Task "
            f"{checksum} has only {count} trial(s), minimum "
            f"{args.min_trials_per_task} required"
            for checksum, count in bad_counts
        )
        raise SystemExit(details)

    print(f"Submission dir: {submission_dir}")
    print(f"Job directories: {len(job_dirs)}")
    print(f"Unique tasks: {len(task_counts)}")
    print(f"Minimum trials per task: {args.min_trials_per_task}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
