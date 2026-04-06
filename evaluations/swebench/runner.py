"""Single-task SWE-bench runner: clone, run agent, extract patch."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluations.bench.exec_prompt import ExecPromptError, run_exec_prompt
from evaluations.swebench.dataset import SWEBenchTask
from evaluations.swebench.prompt import format_swebench_prompt
from just_another_coding_agent.contracts.thinking import ThinkingSetting


@dataclass(frozen=True)
class SWEBenchPrediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str


def prepare_workspace(
    task: SWEBenchTask,
    workspace_dir: Path,
    *,
    repo_cache_dir: Path | None = None,
) -> None:
    if workspace_dir.exists():
        raise FileExistsError(
            f"Workspace directory already exists: {workspace_dir}"
        )

    repo_url = f"https://github.com/{task.repo}.git"
    clone_cmd: list[str] = ["git", "clone", "--quiet"]

    if repo_cache_dir is not None:
        cache_path = repo_cache_dir / task.repo.replace("/", "__")
        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--bare", "--quiet", repo_url, str(cache_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        clone_cmd.extend(["--reference", str(cache_path)])

    clone_cmd.extend([repo_url, str(workspace_dir)])
    subprocess.run(clone_cmd, check=True, capture_output=True, text=True)

    subprocess.run(
        ["git", "checkout", "--quiet", task.base_commit],
        cwd=workspace_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def extract_patch(workspace_dir: Path, base_commit: str) -> str:
    subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "diff", "--cached", base_commit],
        cwd=workspace_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def run_swebench_task(
    task: SWEBenchTask,
    *,
    model: str,
    model_name: str | None = None,
    thinking: ThinkingSetting | None = None,
    workspace_dir: Path,
    sessions_dir: Path,
    include_hints: bool = False,
    popen_factory: Any = subprocess.Popen,
) -> SWEBenchPrediction:
    prompt = format_swebench_prompt(task, include_hints=include_hints)

    try:
        run_exec_prompt(
            prompt=prompt,
            model=model,
            workspace_root=workspace_dir,
            thinking=thinking,
            sessions_root=sessions_dir,
            popen_factory=popen_factory,
        )
    except ExecPromptError:
        pass

    patch = extract_patch(workspace_dir, task.base_commit)

    return SWEBenchPrediction(
        instance_id=task.instance_id,
        model_name_or_path=model_name or model,
        model_patch=patch,
    )


__all__ = [
    "SWEBenchPrediction",
    "extract_patch",
    "prepare_workspace",
    "run_swebench_task",
]
