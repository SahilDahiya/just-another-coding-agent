from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path
from typing import Annotated, Protocol
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.run_events import ShellActivityDetails
from just_another_coding_agent.contracts.sandbox import (
    CommandExecutionApprovalRequest,
)
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools._permissions import (
    describe_shell_permission_delta,
    plan_shell_execution,
    remember_approved_permissions,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
)
from just_another_coding_agent.tools.sandbox_executor import (
    HostSandboxExecutor,
    SandboxCommandRequest,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    truncate_tail_text,
)

SHELL_MAX_LINES = 2000
SHELL_MAX_BYTES = 50 * 1024
SHELL_READER_DRAIN_GRACE_SECONDS = 0.5
# Minimum time between successive partial-update publications. Without
# coalescing, every 4 KB read chunk produces a tool_call_updated event
# carrying the FULL accumulated truncated output, which is O(N²) bytes
# pushed through the RPC pipe and the JSONL writer. Coalescing caps that
# at a few updates per second regardless of how fast the child writes.
SHELL_PUBLISH_MIN_INTERVAL_SECONDS = 0.25


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


def _truncate_shell_output(output: str, *, partial: bool) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=SHELL_MAX_LINES,
        max_bytes=SHELL_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    if partial:
        suffix = "while command is still running"
    else:
        suffix = f"Full output: {_write_full_output(output)}"

    if window.last_line_partial:
        body = (
            f"Showing last {SHELL_MAX_BYTES} bytes of line {window.end_line} "
            "(line exceeds limit)"
        )
    elif window.truncated_by == "lines":
        body = (
            f"Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines}"
        )
    else:
        if partial:
            body = (
                f"Showing lines {window.start_line}-{window.end_line} of "
                f"{window.total_lines} while command is still running "
                f"({SHELL_MAX_BYTES} byte limit)"
            )
        else:
            body = (
                f"Showing lines {window.start_line}-{window.end_line} of "
                f"{window.total_lines} "
                f"({SHELL_MAX_BYTES} byte limit)"
            )

    if partial and window.truncated_by == "bytes" and not window.last_line_partial:
        note = f"[{body}]"
    elif partial:
        note = f"[{body} {suffix}]"
    else:
        note = f"[{body}. {suffix}]"

    return append_tool_note(window.text, note)


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
        {"output": _truncate_shell_output(output, partial=True)},
    )
async def execute_shell(
    *,
    ctx: ShellExecutionContext | None = None,
    workspace_root: Path | str,
    command: str,
    shell_family: ShellFamily,
    timeout: int | None = None,
) -> dict[str, int | str]:
    executor = (
        ctx.deps.sandbox_executor if ctx is not None else HostSandboxExecutor()
    )
    permission_state = (
        ctx.deps.permission_state
        if ctx is not None
        else WorkspaceDeps.from_workspace_root(workspace_root).permission_state
    )
    plan = plan_shell_execution(
        permission_state=permission_state,
        command=command,
        shell_family=shell_family,
        workspace_root=Path(workspace_root),
        permission_memory=(ctx.deps.permission_memory if ctx is not None else None),
    )
    if plan.approval_required:
        if ctx is None or ctx.deps.approval_requester is None:
            raise RuntimeError(
                "Shell execution requires approval, but no approval "
                "requester is configured"
            )
        permission_detail = describe_shell_permission_delta(
            plan.requested_permissions
        )
        reason = f"allow shell command: {truncate_activity_label(command)}"
        if permission_detail:
            reason = f"{reason} ({permission_detail})"
        decision = await ctx.deps.approval_requester(
            CommandExecutionApprovalRequest(
                request_id=f"shell-{uuid4().hex}",
                request_kind="command_execution",
                reason=reason,
                command=command,
                cwd=str(Path(workspace_root).resolve()),
                shell_family=shell_family,
                requested_capabilities=plan.requested_capabilities,
                requested_permissions=plan.requested_permissions,
            )
        )
        if decision.decision != "approved":
            raise RuntimeError(
                "Shell execution approval did not return an approved decision"
            )
        if plan.requested_permissions is not None:
            remember_approved_permissions(
                permission_memory=ctx.deps.permission_memory,
                permissions=plan.requested_permissions,
            )
    handle = await executor.execute(
        SandboxCommandRequest(
            workspace_root=Path(workspace_root),
            command=command,
            shell_family=shell_family,
            permission_state=permission_state,
        )
    )

    output_chunks: list[str] = []
    new_output_event = asyncio.Event()

    async def _read_output() -> None:
        try:
            while True:
                chunk = await handle.read(4096)
                if not chunk:
                    return
                try:
                    text = chunk.decode("utf-8")
                except UnicodeError as error:
                    raise ToolEncodingError(
                        "Command output is not valid UTF-8 text"
                    ) from error
                output_chunks.append(text)
                new_output_event.set()
        finally:
            new_output_event.set()

    async def _publish_loop() -> None:
        # Decoupled from the reader: a slow tool_update_sink must never
        # block stdout draining or delay process completion. Successive
        # publications are also coalesced (min interval) so a fast,
        # high-volume child does not flood the RPC pipe and the JSONL
        # writer with O(N²) growing payloads.
        loop = asyncio.get_running_loop()
        last_publish = 0.0
        while True:
            await new_output_event.wait()
            new_output_event.clear()
            if reader_task.done():
                return
            now = loop.time()
            wait_for = SHELL_PUBLISH_MIN_INTERVAL_SECONDS - (now - last_publish)
            if wait_for > 0:
                try:
                    await asyncio.sleep(wait_for)
                except asyncio.CancelledError:
                    return
                if reader_task.done():
                    return
            await _publish_shell_update(
                ctx=ctx,
                output="".join(output_chunks),
            )
            last_publish = loop.time()

    reader_task = asyncio.create_task(_read_output())
    publisher_task = asyncio.create_task(_publish_loop())
    wait_task = asyncio.create_task(handle.wait())

    async def _await_process_and_drain() -> None:
        # Wait for either the process to exit or the reader to fail.
        done, _pending = await asyncio.wait(
            {wait_task, reader_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # If the reader failed (e.g., decode error) before the process
        # exited, surface that exception now.
        if reader_task in done and reader_task.exception() is not None:
            raise reader_task.exception()  # type: ignore[misc]
        # Process has exited. Give the reader a brief grace period to
        # drain any remaining buffered bytes, then stop waiting on it
        # regardless of whether EOF actually arrived. This prevents
        # hangs from any "pipe never EOFs" condition (e.g., a child
        # that leaked stdout to a long-lived helper).
        if not reader_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(reader_task),
                    timeout=SHELL_READER_DRAIN_GRACE_SECONDS,
                )
            except TimeoutError:
                reader_task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, ToolEncodingError
                ):
                    await reader_task
        if reader_task.done() and not reader_task.cancelled():
            exc = reader_task.exception()
            if exc is not None:
                raise exc

    try:
        if timeout is None:
            await _await_process_and_drain()
        else:
            try:
                await asyncio.wait_for(
                    _await_process_and_drain(), timeout=timeout
                )
            except TimeoutError as error:
                await handle.terminate()
                if not reader_task.done():
                    reader_task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, ToolEncodingError
                ):
                    await reader_task
                output = _truncate_shell_output("".join(output_chunks), partial=False)
                raise ToolCommandError(
                    _format_shell_failure(
                        output,
                        f"Command timed out after {timeout} seconds",
                    )
                ) from error
    except ToolEncodingError:
        await handle.terminate()
        raise
    finally:
        if handle.exit_code is None:
            await handle.terminate()
        if not wait_task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await wait_task
        if not publisher_task.done():
            publisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await publisher_task

    output = _truncate_shell_output("".join(output_chunks), partial=False)

    exit_code = handle.exit_code
    if exit_code is None:
        raise RuntimeError("sandbox executor must report an exit code after wait")

    if exit_code != 0:
        raise ToolCommandError(
            _format_shell_failure(
                output,
                f"Command exited with code {exit_code}",
            )
        )

    return {
        "exit_code": exit_code,
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
