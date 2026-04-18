from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from just_another_coding_agent._pdeathsig import set_pdeathsig_in_child
from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    NormalizedSandboxPolicy,
)
from just_another_coding_agent.tools.windows_search_tools import (
    build_tool_process_env,
    ensure_windows_search_tool,
)


@dataclass(frozen=True)
class SandboxCommandRequest:
    workspace_root: Path
    command: str
    shell_family: ShellFamily
    selected_sandbox_mode: str
    normalized_policy: NormalizedSandboxPolicy


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
_LOCAL_SANDBOX_TERMINATE_TIMEOUT_SECONDS = 5.0


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
    image: str

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
        try:
            await asyncio.wait_for(
                rm_process.wait(),
                timeout=_LOCAL_SANDBOX_TERMINATE_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            rm_process.kill()
            with contextlib.suppress(ProcessLookupError):
                await rm_process.wait()
            raise RuntimeError(
                "Timed out while force-removing local sandbox container "
                f"{self.container_name!r}; check Docker daemon health"
            ) from error
        await self.process.wait()

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode


def _local_sandbox_mount_mode(filesystem_access: str) -> str:
    if filesystem_access == "read_only":
        return "ro"
    if filesystem_access == "workspace_write":
        return "rw"
    raise RuntimeError(
        "Local restricted sandbox does not support filesystem access mode "
        f"{filesystem_access!r}"
    )


def _local_sandbox_failure_guidance(
    *,
    output: str,
    image: str,
    exit_code: int,
) -> str | None:
    lower_output = output.lower()
    if exit_code != 125:
        return None
    if (
        "pull access denied" in lower_output
        or "requested access to the resource is denied" in lower_output
        or "authentication required" in lower_output
        or "unauthorized" in lower_output
        or "denied:" in lower_output
    ):
        return (
            "Local restricted sandbox could not pull Docker image "
            f"{image!r}. Check Docker login and registry access, or set "
            "JACA_SANDBOX_IMAGE to an accessible image."
        )
    if (
        "manifest unknown" in lower_output
        or "manifest for" in lower_output and "not found" in lower_output
        or "no such image" in lower_output
    ):
        return (
            "Local restricted sandbox could not find Docker image "
            f"{image!r}. Confirm the image name or set JACA_SANDBOX_IMAGE "
            "to an existing image."
        )
    if "toomanyrequests" in lower_output or "too many requests" in lower_output:
        return (
            "Local restricted sandbox hit a Docker registry rate limit while "
            f"pulling {image!r}. Retry later, authenticate to the registry, "
            "or use a pre-pulled image."
        )
    return None


def describe_sandbox_failure(
    *,
    handle: SandboxCommandHandle,
    output: str,
    exit_code: int,
) -> str:
    if isinstance(handle, _DockerSandboxCommandHandle):
        guidance = _local_sandbox_failure_guidance(
            output=output,
            image=handle.image,
            exit_code=exit_code,
        )
        if guidance is not None and guidance not in output:
            if output:
                return f"{output}\n\n{guidance}"
            return guidance
    return output


def select_sandbox_executor(
    *,
    configured_executor: SandboxExecutor | None,
    selected_sandbox_mode: str,
    normalized_policy: NormalizedSandboxPolicy,
) -> SandboxExecutor:
    if configured_executor is not None and not isinstance(
        configured_executor, HostSandboxExecutor
    ):
        return configured_executor
    if normalized_policy.execution_isolation == "unsandboxed":
        return configured_executor or HostSandboxExecutor()
    if selected_sandbox_mode == "external":
        raise RuntimeError(
            "External sandbox policy requires an externally managed executor"
        )
    if normalized_policy.execution_isolation == "sandboxed":
        return LocalRestrictedSandboxExecutor()
    raise RuntimeError(
        "Unsupported sandbox policy for local shell execution: "
        f"{selected_sandbox_mode!r}"
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
        normalized_policy = request.normalized_policy
        if (
            normalized_policy.filesystem.extra_read_roots
            or normalized_policy.filesystem.extra_write_roots
        ):
            raise RuntimeError(
                "Local restricted sandbox executor does not yet support "
                "additional filesystem roots"
            )
        mount_mode = _local_sandbox_mount_mode(
            normalized_policy.filesystem.access
        )
        docker_args = [
            "docker",
            "run",
            "--pull",
            "missing",
            "--rm",
            "--name",
            container_name,
            "--interactive",
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
        ]
        if normalized_policy.network.access == "restricted":
            docker_args.extend(["--network", "none"])
        docker_args.extend(
            [
                self._image,
                "bash",
                "-lc",
                request.command,
            ]
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
                "Docker before using the default permission mode."
            ) from error
        if process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return _DockerSandboxCommandHandle(
            process=process,
            container_name=container_name,
            image=self._image,
        )


__all__ = [
    "HostSandboxExecutor",
    "LocalRestrictedSandboxExecutor",
    "SandboxCommandHandle",
    "SandboxCommandRequest",
    "SandboxExecutor",
    "describe_sandbox_failure",
    "select_sandbox_executor",
]
