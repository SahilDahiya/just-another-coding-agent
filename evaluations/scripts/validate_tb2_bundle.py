#!/usr/bin/env python3
"""Validate Terminal Bench submission bundle job consistency.

read_when: validating Harbor submission bundles before recording or upload
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path


def _load_job_task_map(job_dir: Path) -> dict[str, str]:
    task_map: dict[str, str] = {}
    for trial_dir in sorted(job_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        result_path = trial_dir / "result.json"
        if not result_path.exists():
            continue
        data = json.loads(result_path.read_text(encoding="utf-8"))
        task_name = data.get("task_name")
        task_checksum = data.get("task_checksum")
        if not isinstance(task_name, str) or not isinstance(task_checksum, str):
            raise SystemExit(
                "Invalid result payload in "
                f"{result_path}: missing task_name/task_checksum."
            )
        previous = task_map.get(task_name)
        if previous is not None and previous != task_checksum:
            raise SystemExit(
                "Job "
                f"{job_dir.name} contains multiple checksums for task "
                f"{task_name!r}."
            )
        task_map[task_name] = task_checksum
    if not task_map:
        raise SystemExit(f"Job {job_dir} has no readable trial result.json files.")
    return task_map


def _validate_job_dirs(job_dirs: Iterable[Path]) -> None:
    baseline_name: str | None = None
    baseline_map: dict[str, str] | None = None

    for job_dir in job_dirs:
        task_map = _load_job_task_map(job_dir)
        if baseline_map is None:
            baseline_name = job_dir.name
            baseline_map = task_map
            continue

        if set(task_map) != set(baseline_map):
            missing = sorted(set(baseline_map) - set(task_map))
            extra = sorted(set(task_map) - set(baseline_map))
            details: list[str] = []
            if missing:
                details.append(f"missing tasks: {', '.join(missing)}")
            if extra:
                details.append(f"extra tasks: {', '.join(extra)}")
            raise SystemExit(
                f"Job {job_dir.name} does not match baseline job {baseline_name}. "
                + "; ".join(details)
            )

        mismatches = [
            task_name
            for task_name, checksum in sorted(task_map.items())
            if baseline_map[task_name] != checksum
        ]
        if mismatches:
            mismatch_text = ", ".join(
                f"{task_name} ({baseline_map[task_name]} != {task_map[task_name]})"
                for task_name in mismatches
            )
            raise SystemExit(
                f"Job {job_dir.name} has checksum drift relative to baseline job "
                f"{baseline_name}: {mismatch_text}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Terminal Bench submission bundle job consistency."
    )
    parser.add_argument("job_dirs", nargs="+", help="Harbor job directories to compare")
    args = parser.parse_args()

    job_dirs = [Path(job_dir) for job_dir in args.job_dirs]
    for job_dir in job_dirs:
        if not job_dir.is_dir():
            raise SystemExit(f"Job directory does not exist: {job_dir}")

    _validate_job_dirs(job_dirs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
