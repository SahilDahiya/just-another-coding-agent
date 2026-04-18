from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import ReadActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    shorten_path,
    truncate_activity_label,
)
from just_another_coding_agent.tools._permissions import (
    read_only_filesystem_policy,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.protocol import (
    ReadCallResult,
    ReadWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
)

READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024


async def _execute_read_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
    permission_state,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    response = await read_only_worker.send(
        ReadWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(workspace_root),
            filesystem_policy=read_only_filesystem_policy(permission_state),
            path=path,
            offset=offset,
            limit=limit,
            max_lines=READ_MAX_LINES,
            max_bytes=READ_MAX_BYTES,
        )
    )
    if not isinstance(response, ReadCallResult):
        raise RuntimeError(
            "Read-only worker returned the wrong response type for read: "
            f"{type(response).__name__}"
        )
    return _render_read_call_result(response)


def _render_read_call_result(result: ReadCallResult) -> str:
    if result.total_lines == 0:
        return ""

    if result.first_line_exceeds_max_bytes:
        return (
            f"[Line {result.start_line} exceeds {READ_MAX_BYTES} byte limit. "
            "Use shell to read a narrower slice.]"
        )

    if result.truncated:
        return append_tool_note(
            result.window_text,
            (
                f"[Showing lines {result.start_line}-{result.end_line} of "
                f"{result.total_lines}. Use offset={result.next_offset} to continue.]"
            ),
        )

    if result.next_offset is not None and result.end_line < result.total_lines:
        remaining_lines = result.total_lines - result.end_line
        return append_tool_note(
            result.window_text,
            (
                f"[{remaining_lines} more lines in file. "
                f"Use offset={result.next_offset} to continue.]"
            ),
        )

    return result.window_text

async def read(
    ctx: RunContext[WorkspaceDeps],
    path: Annotated[str, Field(min_length=1)],
    offset: Annotated[int | None, Field(ge=1)] = None,
    limit: Annotated[int | None, Field(ge=1)] = None,
) -> str:
    """Read a UTF-8 text file in bounded line windows.

    Args:
        path: Path to the file to read, relative to the workspace root or absolute.
        offset: Optional 1-indexed line number to start reading from.
        limit: Optional maximum number of lines to read before read's own
            truncation ceiling.
    """

    result = await _execute_read_async(
        read_only_worker=ctx.deps.read_only_worker,
        workspace_root=ctx.deps.workspace_root,
        permission_state=ctx.deps.permission_state,
        path=path,
        offset=offset,
        limit=limit,
    )
    return make_tool_return(
        return_value=result,
        title=f"read {truncate_activity_label(path)}",
        summary="read completed",
        details=ReadActivityDetails(
            path=path,
            short_path=shorten_path(path, str(ctx.deps.workspace_root)),
            offset=offset,
            limit=limit,
        ),
    )


READ_TOOL = Tool(
    read,
    takes_ctx=True,
    name="read",
    description=(
        "Read a UTF-8 text file. Supports line-based offset and limit. "
        "When limit is omitted, output is bounded to 2000 lines or 50 KiB "
        "with continuation hints using offset."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=False,
)

__all__ = ["READ_TOOL", "read"]
