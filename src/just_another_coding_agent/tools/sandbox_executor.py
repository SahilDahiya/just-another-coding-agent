from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from just_another_coding_agent._pdeathsig import set_pdeathsig_in_child
from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import PermissionState
from just_another_coding_agent.tools.windows_search_tools import (
    build_tool_process_env,
    ensure_windows_search_tool,
)


@dataclass(frozen=True)
class SandboxCommandRequest:
    workspace_root: Path
    command: str
    shell_family: ShellFamily
    permission_state: PermissionState


class SandboxCommandHandle(Protocol):
    async def read(self, max_bytes: int) -> bytes: ...

    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...

    @property
    def exit_code(self) -> int | None: ...


class SandboxExecutor(Protocol):
    async def execute(self, request: SandboxCommandRequest) -> SandboxCommandHandle: ...


DEFAULT_LOCAL_SANDBOX_IMAGE = os.environ.get(
    "JACA_SANDBOX_IMAGE",
    "docker.io/library/bash:5.2",
)
_LOCAL_SANDBOX_WORKDIR = "/workspace"
_LOCAL_SANDBOX_HOME = "/tmp"


def _shell_command_prefix(shell_family: ShellFamily) -> tuple[str, ...]:
    if shell_family == "powershell":
        executable = "powershell.exe" if os.name == "nt" else "pwsh"
        return (executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
    return ("bash", "-lc")


def _shell_process_kwargs(shell_family: ShellFamily) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if shell_family == "powershell" and os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return kwargs


@dataclass
class _HostSandboxCommandHandle:
    process: asyncio.subprocess.Process
    shell_family: ShellFamily

    async def read(self, max_bytes: int) -> bytes:
        if self.process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return await self.process.stdout.read(max_bytes)

    async def wait(self) -> int:
        return await self.process.wait()

    async def terminate(self) -> None:
        if self.process.returncode is not None:
            return

        if self.shell_family == "powershell" and os.name == "nt":
            taskkill = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(self.process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await taskkill.wait()
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except PermissionError:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

        await self.process.wait()

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode


class HostSandboxExecutor:
    async def execute(self, request: SandboxCommandRequest) -> SandboxCommandHandle:
        if os.name == "nt":
            ensure_windows_search_tool("fd", silent=True)
            ensure_windows_search_tool("rg", silent=True)

        spawn_kwargs: dict[str, object] = dict(
            cwd=str(request.workspace_root),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=build_tool_process_env(),
            start_new_session=(request.shell_family == "posix"),
            **_shell_process_kwargs(request.shell_family),
        )
        if os.name != "nt":
            spawn_kwargs["preexec_fn"] = set_pdeathsig_in_child

        process = await asyncio.create_subprocess_exec(
            *_shell_command_prefix(request.shell_family),
            request.command,
            **spawn_kwargs,
        )
        if process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")

        return _HostSandboxCommandHandle(
            process=process,
            shell_family=request.shell_family,
        )


@dataclass
class _DockerSandboxCommandHandle:
    process: asyncio.subprocess.Process
    container_name: str

    async def read(self, max_bytes: int) -> bytes:
        if self.process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return await self.process.stdout.read(max_bytes)

    async def wait(self) -> int:
        return await self.process.wait()

    async def terminate(self) -> None:
        if self.process.returncode is not None:
            return

        rm_process = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            self.container_name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=build_tool_process_env(),
            start_new_session=True,
            preexec_fn=set_pdeathsig_in_child,
        )
        await rm_process.wait()
        await self.process.wait()

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode


def _local_sandbox_mount_mode(request: SandboxCommandRequest) -> str:
    mode = request.permission_state.sandbox_policy.mode
    if mode == "read_only":
        return "ro"
    if mode == "workspace_write":
        return "rw"
    raise RuntimeError(
        f"Local restricted sandbox does not support sandbox policy mode {mode!r}"
    )


def select_sandbox_executor(
    *,
    configured_executor: SandboxExecutor | None,
    permission_state: PermissionState,
) -> SandboxExecutor:
    if configured_executor is not None and not isinstance(
        configured_executor, HostSandboxExecutor
    ):
        return configured_executor
    if permission_state.sandbox_policy.mode == "danger_full_access":
        return configured_executor or HostSandboxExecutor()
    if permission_state.sandbox_policy.mode in {
        "read_only",
        "workspace_write",
    }:
        return LocalRestrictedSandboxExecutor()
    if permission_state.sandbox_policy.mode == "external":
        raise RuntimeError(
            "External sandbox policy requires an externally managed executor"
        )
    raise RuntimeError(
        "Unsupported sandbox policy for local shell execution: "
        f"{permission_state.sandbox_policy.mode!r}"
    )


class LocalRestrictedSandboxExecutor:
    def __init__(self, *, image: str = DEFAULT_LOCAL_SANDBOX_IMAGE) -> None:
        self._image = image

    async def execute(self, request: SandboxCommandRequest) -> SandboxCommandHandle:
        if request.shell_family != "posix":
            raise RuntimeError(
                "Local restricted sandbox executor currently supports only "
                "posix shell execution"
            )
        if os.name == "nt":
            raise RuntimeError(
                "Local restricted sandbox executor is not supported on Windows"
            )

        container_name = f"jaca-sandbox-{uuid4().hex[:12]}"
        mount_mode = _local_sandbox_mount_mode(request)
        docker_args = (
            "docker",
            "run",
            "--pull",
            "never",
            "--rm",
            "--name",
            container_name,
            "--interactive",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=64m",
            "--pids-limit",
            "256",
            "--memory",
            "512m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--workdir",
            _LOCAL_SANDBOX_WORKDIR,
            "--volume",
            (
                f"{request.workspace_root}:{_LOCAL_SANDBOX_WORKDIR}:"
                f"{mount_mode}"
            ),
            "--env",
            f"HOME={_LOCAL_SANDBOX_HOME}",
            self._image,
            "bash",
            "-lc",
            request.command,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *docker_args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=build_tool_process_env(),
                start_new_session=True,
                preexec_fn=set_pdeathsig_in_child,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                "Local restricted sandbox executor requires Docker. Install "
                "Docker and pre-pull the configured sandbox image before "
                "using the default permission mode."
            ) from error
        if process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return _DockerSandboxCommandHandle(
            process=process,
            container_name=container_name,
        )


__all__ = [
    "HostSandboxExecutor",
    "LocalRestrictedSandboxExecutor",
    "SandboxCommandHandle",
    "SandboxCommandRequest",
    "SandboxExecutor",
    "select_sandbox_executor",
]
