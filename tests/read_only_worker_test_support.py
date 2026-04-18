from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalPolicy,
    FileSystemSandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)

READ_ONLY_WORKER_BINARY = (
    "jaca-read-only-worker.exe" if os.name == "nt" else "jaca-read-only-worker"
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _worker_build_dir() -> Path:
    return repo_root() / ".pytest_cache" / "jaca-test-bin"


@lru_cache(maxsize=1)
def ensure_built_read_only_worker() -> Path:
    executable = _worker_build_dir() / READ_ONLY_WORKER_BINARY
    executable.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("CGO_ENABLED", "0")
    completed = subprocess.run(
        ["go", "build", "-o", str(executable), "./cmd/jaca-read-only-worker"],
        cwd=repo_root(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"go build exited with status {completed.returncode}"
        )
        raise RuntimeError(f"failed to build read-only worker test binary: {detail}")
    if not executable.is_file():
        raise RuntimeError(
            f"read-only worker test binary was not created: {executable}"
        )
    if os.name != "nt":
        executable.chmod(executable.stat().st_mode | 0o111)
    return executable


def read_only_worker_command() -> list[str]:
    return [str(ensure_built_read_only_worker())]


def default_read_only_worker_filesystem_policy() -> FileSystemSandboxPolicy:
    return FileSystemSandboxPolicy(access="workspace_write")


def default_read_only_worker_permission_state():
    return build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(network_access="restricted"),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )


def workspace_deps(workspace_root: Path) -> WorkspaceDeps:
    return WorkspaceDeps(
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
        permission_state=default_read_only_worker_permission_state(),
        read_only_worker=ReadOnlyWorkerRuntime(command=read_only_worker_command()),
    )


go_worker_required = pytest.mark.skipif(
    shutil.which("go") is None,
    reason="go required for worker-backed read-only tool tests",
)
