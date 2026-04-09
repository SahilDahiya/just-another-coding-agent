from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.run_events import ShellActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    truncate_tail_text,
)

SHELL_MAX_LINES = 2000
SHELL_MAX_BYTES = 50 * 1024


class ShellExecutionContext(Protocol):
    deps: WorkspaceDeps
    tool_call_id: str | None
    tool_name: str | None


def _format_shell_failure(output: str, failure_message: str) -> str:
    if output:
        return f"{output}\n\n{failure_message}"
    return failure_message


def _write_full_output(output: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="just-another-coding-agent-shell-",
        suffix=".log",
        delete=False,
    ) as file_handle:
        file_handle.write(output)
        return file_handle.name


def _truncate_shell_output(output: str) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=SHELL_MAX_LINES,
        max_bytes=SHELL_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    full_output_path = _write_full_output(output)

    if window.last_line_partial:
        note = (
            f"[Showing last {SHELL_MAX_BYTES} bytes of line {window.end_line} "
            f"(line exceeds limit). Full output: {full_output_path}]"
        )
    elif window.truncated_by == "lines":
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines}. "
            f"Full output: {full_output_path}]"
        )
    else:
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines} "
            f"({SHELL_MAX_BYTES} byte limit). Full output: {full_output_path}]"
        )

    return append_tool_note(window.text, note)


def _truncate_partial_shell_output(output: str) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=SHELL_MAX_LINES,
        max_bytes=SHELL_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    if window.last_line_partial:
        note = (
            f"[Showing last {SHELL_MAX_BYTES} bytes of line {window.end_line} "
            "(line exceeds limit) while command is still running]"
        )
    elif window.truncated_by == "lines":
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines} while command is still running]"
        )
    else:
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines} while command is still running "
            f"({SHELL_MAX_BYTES} byte limit)]"
        )

    return append_tool_note(window.text, note)


async def _terminate_process(
    process: asyncio.subprocess.Process,
    *,
    shell_family: ShellFamily,
) -> None:
    if process.returncode is not None:
        return

    if shell_family == "powershell" and os.name == "nt":
        taskkill = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await taskkill.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except PermissionError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    await process.wait()


async def _publish_shell_update(
    *,
    ctx: ShellExecutionContext | None,
    output: str,
) -> None:
    if ctx is None or ctx.deps.tool_update_sink is None:
        return
    if ctx.tool_call_id is None or ctx.tool_name is None:
        return

    await ctx.deps.tool_update_sink(
        ctx.tool_call_id,
        ctx.tool_name,
        {"output": _truncate_partial_shell_output(output)},
    )


def _shell_command_prefix(shell_family: ShellFamily) -> tuple[str, ...]:
    if shell_family == "powershell":
        executable = "powershell.exe" if os.name == "nt" else "pwsh"
        return (executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
    return ("bash", "-lc")


def _shell_process_kwargs(shell_family: ShellFamily) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if shell_family == "powershell" and os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return kwargs


async def execute_shell(
    *,
    ctx: ShellExecutionContext | None = None,
    workspace_root: Path | str,
    command: str,
    shell_family: ShellFamily,
    timeout: int | None = None,
) -> dict[str, int | str]:
    process = await asyncio.create_subprocess_exec(
        *_shell_command_prefix(shell_family),
        command,
        cwd=str(workspace_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=(shell_family == "posix"),
        **_shell_process_kwargs(shell_family),
    )
    if process.stdout is None:
        raise RuntimeError("shell subprocess must expose stdout")

    output_chunks: list[str] = []

    async def _read_output() -> None:
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                return
            try:
                text = chunk.decode("utf-8")
            except UnicodeError as error:
                raise ToolEncodingError(
                    "Command output is not valid UTF-8 text"
                ) from error
            output_chunks.append(text)
            await _publish_shell_update(
                ctx=ctx,
                output="".join(output_chunks),
            )

    reader_task = asyncio.create_task(_read_output())

    try:
        if timeout is None:
            await asyncio.gather(process.wait(), reader_task)
        else:
            try:
                await asyncio.wait_for(
                    asyncio.gather(process.wait(), reader_task),
                    timeout=timeout,
                )
            except TimeoutError as error:
                await _terminate_process(process, shell_family=shell_family)
                if not reader_task.done():
                    reader_task.cancel()
                try:
                    await reader_task
                except (asyncio.CancelledError, ToolEncodingError):
                    pass
                output = _truncate_shell_output("".join(output_chunks))
                raise ToolCommandError(
                    _format_shell_failure(
                        output,
                        f"Command timed out after {timeout} seconds",
                    )
                ) from error
    except ToolEncodingError:
        await _terminate_process(process, shell_family=shell_family)
        raise
    finally:
        if process.returncode is None:
            await _terminate_process(process, shell_family=shell_family)

    output = _truncate_shell_output("".join(output_chunks))

    if process.returncode != 0:
        raise ToolCommandError(
            _format_shell_failure(
                output,
                f"Command exited with code {process.returncode}",
            )
        )

    return {
        "exit_code": process.returncode,
        "output": output,
    }


async def shell(
    ctx: RunContext[WorkspaceDeps],
    command: Annotated[str, Field(min_length=1)],
    timeout: Annotated[int | None, Field(gt=0)] = None,
) -> dict[str, int | str]:
    """Execute one local shell command in the workspace root.

    Args:
        command: Shell command to execute using the configured shell family.
        timeout: Optional timeout in seconds before the command is stopped.
    """
    result = await execute_shell(
        ctx=ctx,
        workspace_root=ctx.deps.workspace_root,
        command=command,
        shell_family=ctx.deps.shell_family,
        timeout=timeout,
    )
    return make_tool_return(
        return_value=result,
        title=f"shell {truncate_activity_label(command)}",
        summary=f"command exited {result['exit_code']}",
        details=ShellActivityDetails(
            command_preview=truncate_activity_label(command),
            shell_family=ctx.deps.shell_family,
            timeout=timeout,
            exit_code=result["exit_code"],
        ),
    )


SHELL_TOOL = Tool(
    shell,
    takes_ctx=True,
    name="shell",
    description=(
        "Execute a local shell command in the workspace root using the "
        "configured shell family. posix commands run with bash; "
        "powershell commands run with PowerShell. Returns combined stdout "
        "and stderr on success. Non-zero exits and timeouts become error "
        "results. Large output is truncated to the last 2000 lines or 50 "
        "KiB, and the full output is saved to a temp file."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = [
    "SHELL_TOOL",
    "execute_shell",
    "shell",
]
