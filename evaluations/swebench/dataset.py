"""Load and filter SWE-bench Lite tasks from HuggingFace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SWEBenchTask:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str


SWEBENCH_LITE_DATASET = "princeton-nlp/SWE-bench_Lite"


def load_swebench_lite(
    *,
    filter_ids: list[str] | None = None,
    dataset_name: str = SWEBENCH_LITE_DATASET,
    _loader: object | None = None,
) -> list[SWEBenchTask]:
    if _loader is not None:
        dataset = _loader(dataset_name, split="test")
    else:
        from datasets import load_dataset

        dataset = load_dataset(dataset_name, split="test")
    tasks: list[SWEBenchTask] = []
    filter_set = set(filter_ids) if filter_ids is not None else None

    for row in dataset:
        instance_id = row["instance_id"]
        if filter_set is not None and instance_id not in filter_set:
            continue
        tasks.append(
            SWEBenchTask(
                instance_id=instance_id,
                repo=row["repo"],
                base_commit=row["base_commit"],
                problem_statement=row["problem_statement"],
                hints_text=row.get("hints_text", ""),
            )
        )

    return tasks


def load_task_ids_from_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


__all__ = [
    "SWEBENCH_LITE_DATASET",
    "SWEBenchTask",
    "load_swebench_lite",
    "load_task_ids_from_file",
]
