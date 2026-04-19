from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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


_LOCAL_SANDBOX_READ_ONLY_ROOT = Path("/")
_LOCAL_SANDBOX_DEFAULT_WRITABLE_ROOTS: tuple[Path, ...] = ()
_LOCAL_SANDBOX_VOLATILE_ENV_OVERRIDES = {
    "GOCACHE": "/tmp/go-build",
    "GOTMPDIR": "/tmp/go-tmp",
    "XDG_CACHE_HOME": "/tmp/.cache",
}


def _default_local_sandbox_executable() -> str:
    configured = os.environ.get("JACA_SANDBOX_EXECUTABLE")
    if configured:
        return configured
    if sys.platform == "darwin":
        return "sandbox-exec"
    return "bwrap"


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


def _local_sandbox_mount_mode(filesystem_access: str) -> str:
    if filesystem_access == "read_only":
        return "ro"
    if filesystem_access == "workspace_write":
        return "rw"
    raise RuntimeError(
        "Local restricted sandbox does not support filesystem access mode "
        f"{filesystem_access!r}"
    )


@dataclass(frozen=True)
class _LocalSandboxBindMount:
    source: Path
    target: Path
    writable: bool = False

    @property
    def bubblewrap_flag(self) -> str:
        return "--bind" if self.writable else "--ro-bind"


def _local_sandbox_should_skip_path_entry(path: Path) -> bool:
    path_str = str(path)
    return path_str.startswith("/mnt/") or path_str.startswith("/media/")


def _local_sandbox_tool_path_dirs(env: dict[str, str]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    path_dirs: list[Path] = []
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry)
        if not candidate.is_absolute():
            continue
        resolved = candidate.resolve()
        if (
            not resolved.exists()
            or not resolved.is_dir()
            or _local_sandbox_should_skip_path_entry(resolved)
            or resolved in seen
        ):
            continue
        seen.add(resolved)
        path_dirs.append(resolved)
    return tuple(path_dirs)


def _local_sandbox_env(
    *,
    workspace_root: Path,
) -> tuple[dict[str, str], tuple[Path, ...]]:
    env = build_tool_process_env()
    path_dirs = _local_sandbox_tool_path_dirs(env)
    env["PATH"] = os.pathsep.join(str(path) for path in path_dirs)
    env["PWD"] = str(workspace_root)
    env["TMPDIR"] = "/tmp"
    env["TEMP"] = "/tmp"
    env["TMP"] = "/tmp"
    for key, value in _LOCAL_SANDBOX_VOLATILE_ENV_OVERRIDES.items():
        env[key] = value
    return env, path_dirs


def _local_sandbox_extra_writable_roots(
    *,
    workspace_root: Path,
    normalized_policy: NormalizedSandboxPolicy,
    env: dict[str, str],
) -> tuple[Path, ...]:
    roots: dict[Path, None] = {}

    def _add_root(path: Path) -> None:
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return
        roots[resolved] = None

    _add_root(workspace_root.resolve())
    for root in _LOCAL_SANDBOX_DEFAULT_WRITABLE_ROOTS:
        _add_root(root)
    for key in ("TMPDIR", "TEMP", "TMP"):
        raw_value = env.get(key)
        if not raw_value:
            continue
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            continue
        _add_root(candidate)
    for root in normalized_policy.filesystem.extra_write_roots:
        _add_root(Path(root))
    return tuple(sorted(roots, key=lambda path: (len(path.parts), str(path))))


def _local_sandbox_register_mount(
    mounts: dict[Path, _LocalSandboxBindMount],
    *,
    path: Path,
    writable: bool,
    target: Path | None = None,
) -> None:
    target_path = target or path
    if not target_path.is_absolute():
        raise RuntimeError(
            "Local restricted sandbox mount target must be absolute: "
            f"{target_path}"
        )
    resolved = path.resolve()
    if not resolved.exists():
        raise RuntimeError(
            "Local restricted sandbox mount root does not exist: "
            f"{resolved}"
        )
    existing = mounts.get(target_path)
    if existing is not None and (existing.writable or not writable):
        return
    if writable and resolved.is_file():
        raise RuntimeError(
            "Local restricted sandbox does not support writable file mounts: "
            f"{resolved}"
        )
    mounts[target_path] = _LocalSandboxBindMount(
        source=resolved,
        target=target_path,
        writable=writable,
    )


def _local_sandbox_bind_mounts(
    *,
    workspace_root: Path,
    normalized_policy: NormalizedSandboxPolicy,
    env: dict[str, str],
) -> tuple[_LocalSandboxBindMount, ...]:
    mounts: dict[Path, _LocalSandboxBindMount] = {
        _LOCAL_SANDBOX_READ_ONLY_ROOT: _LocalSandboxBindMount(
            source=_LOCAL_SANDBOX_READ_ONLY_ROOT.resolve(),
            target=_LOCAL_SANDBOX_READ_ONLY_ROOT,
            writable=False,
        )
    }
    writable = (
        _local_sandbox_mount_mode(normalized_policy.filesystem.access) == "rw"
    )
    if writable:
        for root in _local_sandbox_extra_writable_roots(
            workspace_root=workspace_root,
            normalized_policy=normalized_policy,
            env=env,
        ):
            _local_sandbox_register_mount(mounts, path=root, writable=True)
    return tuple(
        mount
        for _path, mount in sorted(
            mounts.items(),
            key=lambda item: (len(item[0].parts), str(item[0])),
        )
    )


def _local_sandbox_parent_dirs(
    mounts: tuple[_LocalSandboxBindMount, ...],
) -> tuple[Path, ...]:
    parents: set[Path] = set()
    for mount in mounts:
        parent = mount.target.parent
        while parent != parent.parent:
            parents.add(parent)
            parent = parent.parent
    return tuple(sorted(parents, key=lambda path: (len(path.parts), str(path))))


def _local_sandbox_runtime_dirs(env: dict[str, str]) -> tuple[Path, ...]:
    runtime_dirs: set[Path] = set()
    for key in _LOCAL_SANDBOX_VOLATILE_ENV_OVERRIDES:
        raw_value = env.get(key)
        if not raw_value:
            continue
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            continue
        runtime_dirs.add(candidate)
    return tuple(sorted(runtime_dirs, key=lambda path: (len(path.parts), str(path))))


def _local_sandbox_shell_executable(
    *,
    shell_family: ShellFamily,
    env: dict[str, str],
) -> str:
    executable = _shell_command_prefix(shell_family)[0]
    resolved = shutil.which(executable, path=env.get("PATH"))
    if resolved is None:
        raise RuntimeError(
            "Local restricted sandbox could not locate the shell executable "
            f"{executable!r} on PATH"
        )
    return resolved


def describe_sandbox_failure(
    *,
    handle: SandboxCommandHandle,
    output: str,
    exit_code: int,
) -> str:
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
    def __init__(
        self,
        *,
        executable: str | None = None,
    ) -> None:
        self._executable = executable or _default_local_sandbox_executable()

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
        if sys.platform.startswith("linux"):
            return await self._execute_linux(request)
        if sys.platform == "darwin":
            return await self._execute_macos(request)
        raise RuntimeError(
            "Local restricted sandbox executor currently supports only Linux "
            "and macOS"
        )

    async def _execute_linux(
        self,
        request: SandboxCommandRequest,
    ) -> SandboxCommandHandle:
        normalized_policy = request.normalized_policy
        env, _path_dirs = _local_sandbox_env(workspace_root=request.workspace_root)
        bind_mounts = _local_sandbox_bind_mounts(
            workspace_root=request.workspace_root,
            normalized_policy=normalized_policy,
            env=env,
        )
        bwrap_args = [
            self._executable,
            "--die-with-parent",
            "--unshare-all",
            "--hostname",
            "jaca-sandbox",
            "--tmpfs",
            "/tmp",
        ]
        if normalized_policy.network.access == "enabled":
            bwrap_args.append("--share-net")
        for parent in _local_sandbox_parent_dirs(bind_mounts):
            if parent == Path("/tmp"):
                continue
            bwrap_args.extend(["--dir", str(parent)])
        for mount in bind_mounts:
            bwrap_args.extend(
                [
                    mount.bubblewrap_flag,
                    str(mount.source),
                    str(mount.target),
                ]
            )
        for runtime_dir in _local_sandbox_runtime_dirs(env):
            bwrap_args.extend(["--dir", str(runtime_dir)])
        bwrap_args.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
            ]
        )
        bwrap_args.extend(["--chdir", str(request.workspace_root)])
        shell_executable = _local_sandbox_shell_executable(
            shell_family=request.shell_family,
            env=env,
        )
        bwrap_args.extend([shell_executable, "-lc", request.command])
        try:
            process = await asyncio.create_subprocess_exec(
                *bwrap_args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=True,
                preexec_fn=set_pdeathsig_in_child,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                "Local restricted sandbox executor requires bubblewrap "
                f"({self._executable!r}). Install bubblewrap before using "
                "the default or strict permission mode."
            ) from error
        if process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return _HostSandboxCommandHandle(
            process=process,
            shell_family=request.shell_family,
        )

    async def _execute_macos(
        self,
        request: SandboxCommandRequest,
    ) -> SandboxCommandHandle:
        normalized_policy = request.normalized_policy
        env, _path_dirs = _local_sandbox_env(workspace_root=request.workspace_root)
        shell_executable = _local_sandbox_shell_executable(
            shell_family=request.shell_family,
            env=env,
        )
        profile = _macos_sandbox_profile(
            workspace_root=request.workspace_root,
            normalized_policy=normalized_policy,
            env=env,
        )
        sandbox_args = [
            self._executable,
            "-p",
            profile,
            shell_executable,
            "-lc",
            request.command,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *sandbox_args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=str(request.workspace_root),
                start_new_session=True,
                preexec_fn=set_pdeathsig_in_child,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                "Local restricted sandbox executor requires sandbox-exec "
                f"({self._executable!r}). Install Xcode command line tools "
                "or provide a compatible sandbox-exec path before using the "
                "default or strict permission mode."
            ) from error
        if process.stdout is None:
            raise RuntimeError("sandbox command must expose stdout")
        return _HostSandboxCommandHandle(
            process=process,
            shell_family=request.shell_family,
        )


def _macos_sandbox_profile_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _macos_sandbox_profile_path_clause(kind: str, path: Path) -> str:
    return f"({kind} \"{_macos_sandbox_profile_escape(str(path))}\")"


def _macos_sandbox_profile(
    *,
    workspace_root: Path,
    normalized_policy: NormalizedSandboxPolicy,
    env: dict[str, str],
) -> str:
    writable_roots = _local_sandbox_extra_writable_roots(
        workspace_root=workspace_root,
        normalized_policy=normalized_policy,
        env=env,
    )
    clauses = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process*)",
        "(allow signal (target self))",
        "(allow file-read*)",
    ]
    if normalized_policy.network.access == "enabled":
        clauses.append("(allow network-outbound)")
    for root in writable_roots:
        clauses.append(
            "(allow file-write* "
            f"{_macos_sandbox_profile_path_clause('subpath', root)})"
        )
    return "\n".join(clauses)


__all__ = [
    "HostSandboxExecutor",
    "LocalRestrictedSandboxExecutor",
    "SandboxCommandHandle",
    "SandboxCommandRequest",
    "SandboxExecutor",
    "describe_sandbox_failure",
    "select_sandbox_executor",
]
