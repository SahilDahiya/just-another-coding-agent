from __future__ import annotations

from pathlib import Path

from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import ReadActivityDetails
from just_another_coding_agent.contracts.tools import (
    ReadToolInput,
)
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
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    truncate_head_line_window,
)

READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024


def execute_read(*, tool_input: ReadToolInput, workspace_root: Path | str) -> str:
    try:
        path = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=tool_input.path,
        )
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeError as error:
        raise ToolEncodingError(f"{tool_input.path} is not valid UTF-8 text") from error
    except OSError as error:
        reraise_path_error(error)

    if not lines:
        if tool_input.offset not in (None, 1):
            raise ToolOperationalError(
                f"Offset {tool_input.offset} is beyond end of file (0 lines total)"
            )
        return ""

    start_line = tool_input.offset or 1
    start_index = start_line - 1
    if start_index >= len(lines):
        raise ToolOperationalError(
            f"Offset {start_line} is beyond end of file ({len(lines)} lines total)"
        )

    selected_lines = lines[start_index:]
    if tool_input.limit is not None:
        selected_lines = selected_lines[: tool_input.limit]

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

    if tool_input.limit is not None and start_index + len(selected_lines) < len(lines):
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


def read(
    ctx: RunContext[WorkspaceDeps],
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """Read a UTF-8 text file in bounded line windows.

    Args:
        path: Path to the file to read, relative to the workspace root or absolute.
        offset: Optional 1-indexed line number to start reading from.
        limit: Optional maximum number of lines to read before read's own
            truncation ceiling.
    """

    result = execute_read(
        tool_input=ReadToolInput(path=path, offset=offset, limit=limit),
        workspace_root=ctx.deps.workspace_root,
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
