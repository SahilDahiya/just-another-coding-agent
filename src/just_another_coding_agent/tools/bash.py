from __future__ import annotations

import asyncio
import os
import signal
import tempfile
from pathlib import Path

from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import BashActivityDetails
from just_another_coding_agent.contracts.tools import (
    BashToolInput,
)
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

BASH_MAX_LINES = 2000
BASH_MAX_BYTES = 50 * 1024


def _format_bash_failure(output: str, failure_message: str) -> str:
    if output:
        return f"{output}\n\n{failure_message}"
    return failure_message


def _write_full_output(output: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="just-another-coding-agent-bash-",
        suffix=".log",
        delete=False,
    ) as file_handle:
        file_handle.write(output)
        return file_handle.name


def _truncate_bash_output(output: str) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=BASH_MAX_LINES,
        max_bytes=BASH_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    full_output_path = _write_full_output(output)

    if window.last_line_partial:
        note = (
            f"[Showing last {BASH_MAX_BYTES} bytes of line {window.end_line} "
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
            f"({BASH_MAX_BYTES} byte limit). Full output: {full_output_path}]"
        )

    return append_tool_note(window.text, note)


def _truncate_partial_bash_output(output: str) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=BASH_MAX_LINES,
        max_bytes=BASH_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    if window.last_line_partial:
        note = (
            f"[Showing last {BASH_MAX_BYTES} bytes of line {window.end_line} "
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
            f"({BASH_MAX_BYTES} byte limit)]"
        )

    return append_tool_note(window.text, note)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    await process.wait()


async def _publish_bash_update(
    *,
    ctx: RunContext[WorkspaceDeps] | None,
    output: str,
) -> None:
    if ctx is None or ctx.deps.tool_update_sink is None:
        return
    if ctx.tool_call_id is None or ctx.tool_name is None:
        return

    await ctx.deps.tool_update_sink(
        ctx.tool_call_id,
        ctx.tool_name,
        {"output": _truncate_partial_bash_output(output)},
    )


async def execute_bash(
    *,
    ctx: RunContext[WorkspaceDeps] | None = None,
    tool_input: BashToolInput,
    workspace_root: Path | str,
) -> dict[str, int | str]:
    process = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        tool_input.command,
        cwd=str(workspace_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    if process.stdout is None:
        raise RuntimeError("bash subprocess must expose stdout")

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
            await _publish_bash_update(
                ctx=ctx,
                output="".join(output_chunks),
            )

    reader_task = asyncio.create_task(_read_output())

    try:
        if tool_input.timeout is None:
            await asyncio.gather(process.wait(), reader_task)
        else:
            try:
                await asyncio.wait_for(
                    asyncio.gather(process.wait(), reader_task),
                    timeout=tool_input.timeout,
                )
            except TimeoutError as error:
                await _terminate_process(process)
                if not reader_task.done():
                    reader_task.cancel()
                try:
                    await reader_task
                except (asyncio.CancelledError, ToolEncodingError):
                    pass
                output = _truncate_bash_output("".join(output_chunks))
                raise ToolCommandError(
                    _format_bash_failure(
                        output,
                        f"Command timed out after {tool_input.timeout} seconds",
                    )
                ) from error
    except ToolEncodingError:
        await _terminate_process(process)
        raise
    finally:
        if process.returncode is None:
            await _terminate_process(process)

    output = _truncate_bash_output("".join(output_chunks))

    if process.returncode != 0:
        raise ToolCommandError(
            _format_bash_failure(
                output,
                f"Command exited with code {process.returncode}",
            )
        )

    return {
        "exit_code": process.returncode,
        "output": output,
    }


async def bash(
    ctx: RunContext[WorkspaceDeps],
    command: str,
    timeout: int | None = None,
) -> dict[str, int | str]:
    """Execute one local bash command in the workspace root.

    Args:
        command: Bash command to execute with `bash -lc`.
        timeout: Optional timeout in seconds before the command is stopped.
    """

    result = await execute_bash(
        ctx=ctx,
        tool_input=BashToolInput(command=command, timeout=timeout),
        workspace_root=ctx.deps.workspace_root,
    )
    return make_tool_return(
        return_value=result,
        title=f"bash {truncate_activity_label(command)}",
        summary=f"command exited {result['exit_code']}",
        details=BashActivityDetails(
            command_preview=truncate_activity_label(command),
            timeout=timeout,
            exit_code=result["exit_code"],
        ),
    )


BASH_TOOL = Tool(
    bash,
    takes_ctx=True,
    name="bash",
    description=(
        "Execute a local bash command in the workspace root. Returns "
        "combined stdout and stderr on success. Non-zero exits and "
        "timeouts become error results. Large output is truncated to the "
        "last 2000 lines or 50 KiB, and the full output is saved to a "
        "temp file."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
)


__all__ = ["BASH_TOOL", "bash", "execute_bash"]
