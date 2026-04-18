from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from tests.read_only_worker_test_support import (
    default_read_only_worker_permission_state,
    read_only_worker_command,
)
from tests.read_only_worker_test_support import (
    go_worker_required as _go_worker_required,
)

go_worker_required = _go_worker_required


def worker_ctx(tmp_path: Path, *, permission_state=None):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            permission_state=(
                permission_state
                if permission_state is not None
                else default_read_only_worker_permission_state()
            ),
            read_only_worker=ReadOnlyWorkerRuntime(command=read_only_worker_command()),
        ),
        tool_call_id="call-1",
        tool_name="tool",
    )
