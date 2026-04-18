from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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


__all__ = [
    "HostSandboxExecutor",
    "SandboxCommandHandle",
    "SandboxCommandRequest",
    "SandboxExecutor",
]
