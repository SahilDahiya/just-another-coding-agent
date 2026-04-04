"""CLI entry point for SWE-bench Lite evaluation runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run JACA against SWE-bench Lite tasks and produce predictions.",
    )
    parser.add_argument("--model", required=True, help="Model id for the backend")
    parser.add_argument(
        "--model-name",
        default=None,
        help="Model name for predictions (defaults to --model)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for predictions, sessions, and workspaces",
    )
    parser.add_argument(
        "--task-id",
        nargs="*",
        default=None,
        help="Run only these instance IDs",
    )
    parser.add_argument(
        "--task-file",
        type=Path,
        default=None,
        help="File with one instance ID per line",
    )
    parser.add_argument(
        "--n-concurrent",
        type=int,
        default=1,
        help="Number of concurrent tasks (default: 1)",
    )
    parser.add_argument(
        "--thinking",
        default=None,
        help="Thinking setting for the agent",
    )
    parser.add_argument(
        "--include-hints",
        action="store_true",
        help="Include SWE-bench hints in the prompt",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="Keep cloned repos after patch extraction",
    )
    parser.add_argument(
        "--repo-cache-dir",
        type=Path,
        default=None,
        help="Directory for bare repo clones (avoids re-cloning)",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="HuggingFace dataset name (default: SWE-bench Lite)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from evaluations.swebench.batch import run_swebench_batch
    from evaluations.swebench.dataset import load_swebench_lite, load_task_ids_from_file

    filter_ids = list(args.task_id) if args.task_id else None
    if args.task_file is not None:
        file_ids = load_task_ids_from_file(args.task_file)
        if filter_ids is not None:
            filter_ids.extend(file_ids)
        else:
            filter_ids = file_ids

    load_kwargs = {}
    if args.dataset is not None:
        load_kwargs["dataset_name"] = args.dataset
    tasks = load_swebench_lite(filter_ids=filter_ids, **load_kwargs)

    if not tasks:
        print("No tasks matched the filter.", file=sys.stderr)
        return 1

    print(f"Loaded {len(tasks)} SWE-bench tasks.", file=sys.stderr)

    predictions_path = run_swebench_batch(
        tasks=tasks,
        model=args.model,
        model_name=args.model_name,
        thinking=args.thinking,
        output_dir=args.output_dir,
        n_concurrent=args.n_concurrent,
        include_hints=args.include_hints,
        keep_workspaces=args.keep_workspaces,
        repo_cache_dir=args.repo_cache_dir,
    )

    print(f"Predictions written to {predictions_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
