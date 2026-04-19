import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalPolicy,
    NormalizedSandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    build_default_permission_state,
    build_permission_state,
    derive_normalized_sandbox_policy,
)
from just_another_coding_agent.tools import sandbox_executor as sandbox_executor_module
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolCommandError
from just_another_coding_agent.tools.sandbox_executor import (
    HostSandboxExecutor,
    LocalRestrictedSandboxExecutor,
    SandboxCommandRequest,
    describe_sandbox_failure,
)
from just_another_coding_agent.tools.shell import execute_shell


@dataclass(frozen=True)
class _FakeRunContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = None
    tool_name: str | None = None


class _FakeHandle:
    def __init__(
        self,
        *,
        chunks: list[bytes],
        exit_code: int = 0,
        wait_delay_seconds: float = 0.0,
        block_after_chunks_until_terminated: bool = False,
    ) -> None:
        self._chunks = list(chunks)
        self._exit_code = exit_code
        self._wait_delay_seconds = wait_delay_seconds
        self._block_after_chunks_until_terminated = block_after_chunks_until_terminated
        self.terminate_calls = 0
        self.wait_calls = 0
        self._terminated = asyncio.Event()

    async def read(self, _max_bytes: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._block_after_chunks_until_terminated and self.terminate_calls == 0:
            await self._terminated.wait()
        return b""

    async def wait(self) -> int:
        self.wait_calls += 1
        if self._wait_delay_seconds > 0:
            await asyncio.sleep(self._wait_delay_seconds)
        return self._exit_code

    async def terminate(self) -> None:
        self.terminate_calls += 1
        self._terminated.set()

    @property
    def exit_code(self) -> int | None:
        if self.wait_calls > 0 or self.terminate_calls > 0:
            return self._exit_code
        return None


class _FakeExecutor:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle
        self.requests: list[SandboxCommandRequest] = []

    async def execute(self, request: SandboxCommandRequest) -> _FakeHandle:
        self.requests.append(request)
        return self._handle


def _test_shell_family() -> str:
    return detect_default_shell_family()


class _FakeStdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _count: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        returncode: int | None = 0,
    ) -> None:
        self.pid = 123
        self.returncode = returncode
        self.stdout = _FakeStdout(stdout_chunks or [b"ok"])
        self.kill_calls = 0
        self.wait_calls = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        return 0 if self.returncode is None else self.returncode


async def test_execute_shell_delegates_command_start_to_sandbox_executor(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    handle = _FakeHandle(chunks=[b"hello"])
    executor = _FakeExecutor(handle)
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=executor,
        ),
        tool_call_id="tool-1",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="printf hello",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert executor.requests == [
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="printf hello",
            shell_family=_test_shell_family(),
            selected_sandbox_mode="workspace_write",
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=build_default_permission_state()
            ),
        )
    ]
    assert handle.wait_calls == 1


async def test_execute_shell_terminates_through_sandbox_executor_on_timeout(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    handle = _FakeHandle(
        chunks=[b"partial output"],
        wait_delay_seconds=2.0,
        block_after_chunks_until_terminated=True,
    )
    executor = _FakeExecutor(handle)
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=executor,
        ),
        tool_call_id="tool-1",
        tool_name="shell",
    )

    with pytest.raises(
        ToolCommandError,
        match="partial output\n\nCommand timed out after 1 seconds",
    ):
        await execute_shell(
            ctx=ctx,
            workspace_root=workspace_root,
            command="printf partial output",
            shell_family=_test_shell_family(),
            timeout=1,
        )

    assert handle.terminate_calls == 1


async def test_host_sandbox_executor_uses_posix_runner_for_posix_shell_family(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await HostSandboxExecutor().execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="pwd",
            shell_family="posix",
            selected_sandbox_mode="danger_full_access",
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=build_default_permission_state()
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    assert observed["args"][:2] == ("bash", "-lc")


async def test_host_sandbox_executor_uses_managed_tool_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "managed-bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await HostSandboxExecutor().execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="pwd",
            shell_family="posix",
            selected_sandbox_mode="danger_full_access",
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=build_default_permission_state()
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    assert observed["kwargs"]["env"] == {"PATH": "managed-bin"}


async def test_host_sandbox_executor_bootstraps_windows_search_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {"bootstrapped": []}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(sandbox_executor_module.os, "name", "nt")
    monkeypatch.setattr(
        sandbox_executor_module.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_executor_module.subprocess,
        "CREATE_NO_WINDOW",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_executor_module,
        "ensure_windows_search_tool",
        lambda tool, *, silent=True: observed["bootstrapped"].append((tool, silent)),
    )
    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "managed-bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await HostSandboxExecutor().execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="rg needle .",
            shell_family="powershell",
            selected_sandbox_mode="danger_full_access",
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=build_default_permission_state()
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    assert observed["bootstrapped"] == [("fd", True), ("rg", True)]
    assert observed["kwargs"]["env"] == {"PATH": "managed-bin"}


async def test_host_sandbox_handle_kills_child_when_killpg_is_not_permitted(
    monkeypatch,
) -> None:
    if not hasattr(sandbox_executor_module.os, "killpg") or not hasattr(
        sandbox_executor_module.signal,
        "SIGKILL",
    ):
        pytest.skip("killpg path is only exercised on POSIX hosts with SIGKILL")

    process = _FakeProcess(returncode=None)
    handle = sandbox_executor_module._HostSandboxCommandHandle(
        process=process,
        shell_family="posix",
    )

    def _raise_permission_error(_pid: int, _signal: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(
        sandbox_executor_module.os,
        "killpg",
        _raise_permission_error,
        raising=False,
    )

    await handle.terminate()

    assert process.kill_calls == 1
    assert process.wait_calls == 1


async def test_execute_shell_routes_workspace_write_policy_to_restricted_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    handle = _FakeHandle(chunks=[b"hello"])
    executor = _FakeExecutor(handle)

    monkeypatch.setattr(
        sandbox_executor_module,
        "LocalRestrictedSandboxExecutor",
        lambda: executor,
    )

    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
                effective_capabilities=build_default_permission_state()
                .effective_capabilities.model_copy(
                    update={"approval_mode": "on_escalation"}
                ),
            ),
        ),
        tool_call_id="tool-1",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="printf hello",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert executor.requests == [
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="printf hello",
            shell_family=_test_shell_family(),
            selected_sandbox_mode=ctx.deps.permission_state.sandbox_policy.mode,
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=ctx.deps.permission_state
            ),
        )
    ]


def _bind_triplets(args: tuple[object, ...]) -> list[tuple[str, str, str]]:
    triplets: list[tuple[str, str, str]] = []
    for index, value in enumerate(args):
        if value not in {"--bind", "--ro-bind"}:
            continue
        triplets.append(
            (
                str(value),
                str(args[index + 1]),
                str(args[index + 2]),
            )
        )
    return triplets


async def test_local_restricted_sandbox_executor_launches_bubblewrap_with_workspace_bind(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "/usr/bin:/bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.shutil,
        "which",
        lambda executable, path=None: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await LocalRestrictedSandboxExecutor(
        executable="sandbox-executable"
    ).execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="pwd",
            shell_family="posix",
            selected_sandbox_mode="workspace_write",
            normalized_policy=derive_normalized_sandbox_policy(
                permission_state=build_permission_state(
                    sandbox_policy=WorkspaceWriteSandboxPolicy(),
                    approval_policy=ApprovalPolicy(mode="on_escalation"),
                    effective_capabilities=build_default_permission_state()
                    .effective_capabilities.model_copy(
                        update={"approval_mode": "on_escalation"}
                    ),
                )
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    args = observed["args"]
    assert args[0] == "sandbox-executable"
    assert "--unshare-all" in args
    assert "--proc" in args
    assert "--dev" in args
    assert "--tmpfs" in args
    assert "--share-net" not in args
    assert args[args.index("--chdir") + 1] == str(workspace_root)
    assert ("--bind", str(workspace_root), str(workspace_root)) in _bind_triplets(
        args
    )
    if Path("/lib").exists() and Path("/lib").resolve() != Path("/lib"):
        assert (
            "--ro-bind",
            str(Path("/lib").resolve()),
            "/lib",
        ) in _bind_triplets(args)
    if Path("/lib64").exists() and Path("/lib64").resolve() != Path("/lib64"):
        assert (
            "--ro-bind",
            str(Path("/lib64").resolve()),
            "/lib64",
        ) in _bind_triplets(args)
    assert args[-3:] == ("/usr/bin/bash", "-lc", "pwd")


async def test_local_restricted_sandbox_executor_allows_network_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "/usr/bin:/bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.shutil,
        "which",
        lambda executable, path=None: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await LocalRestrictedSandboxExecutor(
        executable="sandbox-executable"
    ).execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="curl https://example.com",
            shell_family="posix",
            selected_sandbox_mode="workspace_write",
            normalized_policy=NormalizedSandboxPolicy.model_validate(
                {
                    "filesystem": {"access": "workspace_write"},
                    "network": {"access": "enabled"},
                    "execution_isolation": "sandboxed",
                }
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    args = observed["args"]
    assert "--share-net" in args
    assert ("--bind", str(workspace_root), str(workspace_root)) in _bind_triplets(
        args
    )
    assert args[-3:] == ("/usr/bin/bash", "-lc", "curl https://example.com")


async def test_local_restricted_sandbox_executor_mounts_approved_extra_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_read_root = tmp_path / "read-root"
    outside_read_root.mkdir()
    outside_write_root = tmp_path / "write-root"
    outside_write_root.mkdir()
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "/usr/bin:/bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.shutil,
        "which",
        lambda executable, path=None: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await LocalRestrictedSandboxExecutor(
        executable="sandbox-executable"
    ).execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command=f"cat {outside_read_root / 'README.md'}",
            shell_family="posix",
            selected_sandbox_mode="workspace_write",
            normalized_policy=NormalizedSandboxPolicy.model_validate(
                {
                    "filesystem": {
                        "access": "workspace_write",
                        "extra_read_roots": [str(outside_read_root)],
                        "extra_write_roots": [str(outside_write_root)],
                    },
                    "network": {"access": "restricted"},
                    "execution_isolation": "sandboxed",
                }
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    args = observed["args"]
    bind_triplets = _bind_triplets(args)
    assert ("--bind", str(workspace_root), str(workspace_root)) in bind_triplets
    assert (
        "--ro-bind",
        str(outside_read_root),
        str(outside_read_root),
    ) in bind_triplets
    assert (
        "--bind",
        str(outside_write_root),
        str(outside_write_root),
    ) in bind_triplets


async def test_local_restricted_sandbox_executor_canonicalizes_symlink_mount_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias_root = tmp_path / "alias-root"
    try:
        alias_root.symlink_to(real_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable in this environment: {error}")
    observed: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "/usr/bin:/bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.shutil,
        "which",
        lambda executable, path=None: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    handle = await LocalRestrictedSandboxExecutor(
        executable="sandbox-executable"
    ).execute(
        SandboxCommandRequest(
            workspace_root=workspace_root,
            command="pwd",
            shell_family="posix",
            selected_sandbox_mode="workspace_write",
            normalized_policy=NormalizedSandboxPolicy.model_validate(
                {
                    "filesystem": {
                        "access": "workspace_write",
                        "extra_read_roots": [str(alias_root)],
                    },
                    "network": {"access": "restricted"},
                    "execution_isolation": "sandboxed",
                }
            ),
        )
    )

    assert await handle.read(4096) == b"ok"
    args = observed["args"]
    bind_triplets = _bind_triplets(args)
    expected_mount = ("--ro-bind", str(real_root), str(real_root))
    assert expected_mount in bind_triplets
    assert all(str(alias_root) not in mount for mount in bind_triplets)


async def test_local_restricted_sandbox_executor_requires_bubblewrap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("missing bwrap")

    monkeypatch.setattr(
        sandbox_executor_module,
        "build_tool_process_env",
        lambda: {"PATH": "/usr/bin:/bin"},
    )
    monkeypatch.setattr(
        sandbox_executor_module.shutil,
        "which",
        lambda executable, path=None: f"/usr/bin/{executable}",
    )
    monkeypatch.setattr(
        sandbox_executor_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(RuntimeError, match="requires bubblewrap"):
        await LocalRestrictedSandboxExecutor(
            executable="missing-bwrap"
        ).execute(
            SandboxCommandRequest(
                workspace_root=workspace_root,
                command="pwd",
                shell_family="posix",
                selected_sandbox_mode="workspace_write",
                normalized_policy=NormalizedSandboxPolicy.model_validate(
                    {
                        "filesystem": {"access": "workspace_write"},
                        "network": {"access": "restricted"},
                        "execution_isolation": "sandboxed",
                    }
                ),
            )
        )


def test_describe_sandbox_failure_leaves_output_unchanged() -> None:
    handle = _FakeHandle(chunks=[])

    result = describe_sandbox_failure(
        handle=handle,
        output="plain failure",
        exit_code=1,
    )

    assert result == "plain failure"
