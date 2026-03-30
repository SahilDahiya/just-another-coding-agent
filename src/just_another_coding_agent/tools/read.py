from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import ReadActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools._workspace import resolve_workspace_path
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolEncodingError,
    ToolOperationalError,
    reraise_path_error,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    ReadCallResult,
    ReadWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    truncate_head_line_window,
)

READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024


async def _execute_read_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    response = await read_only_worker.send(
        ReadWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(workspace_root),
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


def execute_read(
    *,
    workspace_root: Path | str,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    try:
        resolved_path = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path,
        )
        lines = resolved_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeError as error:
        raise ToolEncodingError(f"{path} is not valid UTF-8 text") from error
    except OSError as error:
        reraise_path_error(error)

    if not lines:
        if offset not in (None, 1):
            raise ToolOperationalError(
                f"Offset {offset} is beyond end of file (0 lines total)"
            )
        return ""

    start_line = offset or 1
    start_index = start_line - 1
    if start_index >= len(lines):
        raise ToolOperationalError(
            f"Offset {start_line} is beyond end of file ({len(lines)} lines total)"
        )

    selected_lines = lines[start_index:]
    if limit is not None:
        selected_lines = selected_lines[:limit]

    window = truncate_head_line_window(
        selected_lines,
        max_lines=READ_MAX_LINES,
        max_bytes=READ_MAX_BYTES,
    )
    if window.first_line_exceeds_limit:
        return (
            f"[Line {start_line} exceeds {READ_MAX_BYTES} byte limit. "
            "Use shell to read a narrower slice.]"
        )

    if window.truncated:
        end_line = start_index + window.line_count
        return append_tool_note(
            window.text,
            (
                f"[Showing lines {start_line}-{end_line} of {len(lines)}. "
                f"Use offset={end_line + 1} to continue.]"
            ),
        )

    if limit is not None and start_index + len(selected_lines) < len(lines):
        next_offset = start_index + len(selected_lines) + 1
        remaining_lines = len(lines) - (start_index + len(selected_lines))
        return append_tool_note(
            window.text,
            (
                f"[{remaining_lines} more lines in file. "
                f"Use offset={next_offset} to continue.]"
            ),
        )

    return window.text


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
        path=path,
        offset=offset,
        limit=limit,
    )
    return make_tool_return(
        return_value=result,
        title=f"read {truncate_activity_label(path)}",
        summary="read completed",
        details=ReadActivityDetails(path=path, offset=offset, limit=limit),
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


__all__ = ["READ_TOOL", "execute_read", "read"]
