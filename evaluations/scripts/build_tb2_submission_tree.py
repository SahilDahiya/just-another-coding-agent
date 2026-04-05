#!/usr/bin/env python3
"""Assemble a Terminal Bench submission tree from local Harbor jobs.

read_when: preparing one clean final submission tree before opening a PR
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _read_completed_jobs(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _bundle_job_names(bundle_dir: Path) -> list[str]:
    job_names: list[str] = []
    seen: set[str] = set()

    def record(path: Path) -> None:
        for job_name in _read_completed_jobs(path):
            if job_name in seen:
                raise SystemExit(f"Duplicate job recorded in bundle inputs: {job_name}")
            seen.add(job_name)
            job_names.append(job_name)

    record(bundle_dir / "completed-jobs.txt")

    slices_dir = bundle_dir / "slices"
    if slices_dir.is_dir():
        for completed_jobs in sorted(slices_dir.glob("*/completed-jobs.txt")):
            record(completed_jobs)

    if not job_names:
        raise SystemExit(f"No completed jobs found under bundle dir {bundle_dir}")
    return job_names


def _write_metadata(path: Path, args: argparse.Namespace) -> None:
    metadata = "\n".join(
        [
            f"agent_url: {args.agent_url}",
            f'agent_display_name: "{args.agent_display_name}"',
            f'agent_org_display_name: "{args.agent_org_display_name}"',
            "",
            "models:",
            f"  - model_name: {args.model_name}",
            f"    model_provider: {args.model_provider}",
            f'    model_display_name: "{args.model_display_name}"',
            f'    model_org_display_name: "{args.model_org_display_name}"',
            "",
        ]
    )
    path.write_text(metadata, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble a clean Terminal Bench submission tree from local Harbor jobs."
        )
    )
    parser.add_argument(
        "output_dir",
        help=(
            "Final submission directory such as "
            "submissions/terminal-bench/2.0/<agent>__<model>"
        ),
    )
    parser.add_argument(
        "--jobs-dir",
        default="jobs",
        help="Local Harbor jobs root. Defaults to ./jobs",
    )
    parser.add_argument(
        "--bundle-dir",
        action="append",
        default=[],
        help=(
            "Submission bundle dir containing completed-jobs.txt and/or "
            "slices/*/completed-jobs.txt"
        ),
    )
    parser.add_argument(
        "--job-dir",
        action="append",
        default=[],
        help="Explicit Harbor job dir to include. Can be repeated.",
    )
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--agent-display-name", required=True)
    parser.add_argument("--agent-org-display-name", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model-display-name", required=True)
    parser.add_argument("--model-org-display-name", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    jobs_dir = Path(args.jobs_dir)

    if output_dir.exists():
        raise SystemExit(f"Output dir already exists: {output_dir}")

    job_dirs: list[Path] = []
    seen_job_names: set[str] = set()

    for bundle_dir_text in args.bundle_dir:
        bundle_dir = Path(bundle_dir_text)
        for job_name in _bundle_job_names(bundle_dir):
            if job_name in seen_job_names:
                raise SystemExit(f"Duplicate job selected for output: {job_name}")
            seen_job_names.add(job_name)
            job_dirs.append(jobs_dir / job_name)

    for job_dir_text in args.job_dir:
        job_dir = Path(job_dir_text)
        job_name = job_dir.name
        if job_name in seen_job_names:
            raise SystemExit(f"Duplicate job selected for output: {job_name}")
        seen_job_names.add(job_name)
        job_dirs.append(job_dir)

    if not job_dirs:
        raise SystemExit("No jobs selected. Provide --bundle-dir and/or --job-dir.")

    for job_dir in job_dirs:
        if not job_dir.is_dir():
            raise SystemExit(f"Job dir does not exist: {job_dir}")

    output_dir.mkdir(parents=True)
    _write_metadata(output_dir / "metadata.yaml", args)
    (output_dir / ".gitattributes").write_text(
        "*.jsonl filter=lfs diff=lfs merge=lfs -text\n",
        encoding="utf-8",
    )

    for job_dir in job_dirs:
        shutil.copytree(job_dir, output_dir / job_dir.name)

    print(f"Built submission tree: {output_dir}")
    print(f"Copied jobs: {len(job_dirs)}")
    for job_dir in job_dirs:
        print(job_dir.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
