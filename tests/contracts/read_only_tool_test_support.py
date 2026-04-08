from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_only_worker_command() -> list[str]:
    repo_root = _repo_root()
    launcher = (
        "import os, sys; "
        "os.chdir(sys.argv[1]); "
        "os.execvp('go', ['go', 'run', './cmd/jaca-read-only-worker'])"
    )
    return [
        sys.executable,
        "-c",
        launcher,
        str(repo_root),
    ]


def worker_ctx(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            read_only_worker=ReadOnlyWorkerRuntime(command=read_only_worker_command()),
        ),
        tool_call_id="call-1",
        tool_name="tool",
    )


go_worker_required = pytest.mark.skipif(
    shutil.which("go") is None,
    reason="go required for worker-backed read-only tool tests",
)
