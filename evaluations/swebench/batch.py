"""Batch orchestrator for SWE-bench Lite tasks with resume support."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from just_another_coding_agent.contracts.thinking import ThinkingSetting

from evaluations.swebench.dataset import SWEBenchTask
from evaluations.swebench.runner import (
    SWEBenchPrediction,
    prepare_workspace,
    run_swebench_task,
)

PREDICTIONS_FILENAME = "predictions.jsonl"
COMPLETED_TASKS_FILENAME = "completed-tasks.txt"


def run_swebench_batch(
    *,
    tasks: list[SWEBenchTask],
    model: str,
    model_name: str | None = None,
    thinking: ThinkingSetting | None = None,
    output_dir: Path,
    n_concurrent: int = 1,
    include_hints: bool = False,
    keep_workspaces: bool = False,
    repo_cache_dir: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = output_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    predictions_path = output_dir / PREDICTIONS_FILENAME
    completed_path = output_dir / COMPLETED_TASKS_FILENAME

    completed_ids = _load_completed_ids(completed_path)
    pending = [task for task in tasks if task.instance_id not in completed_ids]

    if not pending:
        return predictions_path

    if n_concurrent <= 1:
        for task in pending:
            _run_one_task(
                task=task,
                model=model,
                model_name=model_name,
                thinking=thinking,
                tasks_dir=tasks_dir,
                predictions_path=predictions_path,
                completed_path=completed_path,
                include_hints=include_hints,
                keep_workspaces=keep_workspaces,
                repo_cache_dir=repo_cache_dir,
            )
    else:
        with ProcessPoolExecutor(max_workers=n_concurrent) as executor:
            futures = {
                executor.submit(
                    _run_one_task,
                    task=task,
                    model=model,
                    model_name=model_name,
                    thinking=thinking,
                    tasks_dir=tasks_dir,
                    predictions_path=predictions_path,
                    completed_path=completed_path,
                    include_hints=include_hints,
                    keep_workspaces=keep_workspaces,
                    repo_cache_dir=repo_cache_dir,
                ): task
                for task in pending
            }
            for future in as_completed(futures):
                future.result()

    return predictions_path


def _run_one_task(
    *,
    task: SWEBenchTask,
    model: str,
    model_name: str | None,
    thinking: ThinkingSetting | None,
    tasks_dir: Path,
    predictions_path: Path,
    completed_path: Path,
    include_hints: bool,
    keep_workspaces: bool,
    repo_cache_dir: Path | None,
    popen_factory: Any = None,
) -> None:
    import subprocess

    task_dir = tasks_dir / task.instance_id
    workspace_dir = task_dir / "workspace"
    sessions_dir = task_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    effective_popen_factory = popen_factory or subprocess.Popen

    prepare_workspace(
        task,
        workspace_dir,
        repo_cache_dir=repo_cache_dir,
    )

    prediction = run_swebench_task(
        task,
        model=model,
        model_name=model_name,
        thinking=thinking,
        workspace_dir=workspace_dir,
        sessions_dir=sessions_dir,
        include_hints=include_hints,
        popen_factory=effective_popen_factory,
    )

    _append_prediction(prediction, predictions_path)
    _record_completed(task.instance_id, completed_path)

    if not keep_workspaces and workspace_dir.exists():
        shutil.rmtree(workspace_dir)


def _append_prediction(prediction: SWEBenchPrediction, path: Path) -> None:
    record = {
        "instance_id": prediction.instance_id,
        "model_name_or_path": prediction.model_name_or_path,
        "model_patch": prediction.model_patch,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _record_completed(instance_id: str, path: Path) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(instance_id + "\n")


__all__ = [
    "COMPLETED_TASKS_FILENAME",
    "PREDICTIONS_FILENAME",
    "run_swebench_batch",
]
