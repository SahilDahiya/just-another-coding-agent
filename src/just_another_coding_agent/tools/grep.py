from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import GrepActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    shorten_path,
    truncate_activity_label,
)
from just_another_coding_agent.tools._permissions import (
    approved_read_only_filesystem_policy,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.protocol import (
    GrepCallResult,
    GrepWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
)
from just_another_coding_agent.tools.windows_search_tools import (
    ensure_windows_search_tool,
)

GREP_MAX_MATCHES = 100
GREP_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_CHARS = 300


async def _execute_grep_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
    filesystem_policy,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    limit: int = GREP_MAX_MATCHES,
) -> str:
    response = await read_only_worker.send(
        GrepWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(workspace_root),
            filesystem_policy=filesystem_policy,
            pattern=pattern,
            path=path,
            glob=glob,
            ignore_case=ignore_case,
            literal=literal,
            limit=limit,
            max_bytes=GREP_MAX_BYTES,
            max_line_chars=GREP_MAX_LINE_CHARS,
        )
    )
    if not isinstance(response, GrepCallResult):
        raise RuntimeError(
            "Read-only worker returned the wrong response type for grep: "
            f"{type(response).__name__}"
        )
    return _render_grep_call_result(response)


def _render_grep_call_result(result: GrepCallResult) -> str:
    if not result.matches:
        if result.byte_limit_hit:
            return (
                f"[Search output exceeded {GREP_MAX_BYTES} bytes before a full "
                "match could be returned. Narrow the pattern or path.]"
            )
        return "No matches found."

    output = "\n".join(
        f"{match.path}:{match.line_number}:{match.text}" for match in result.matches
    )
    notices: list[str] = []
    if result.limit_hit:
        notices.append(
            f"Showing first {len(result.matches)} matches. "
            "Refine pattern or path to narrow results."
        )
    if result.byte_limit_hit:
        notices.append(
            f"Search output exceeded {GREP_MAX_BYTES} bytes. Refine pattern or path."
        )
    if result.truncated_lines:
        notices.append(
            f"Some match lines were truncated to {GREP_MAX_LINE_CHARS} characters."
        )
    if notices:
        output = append_tool_note(output, f"[{' '.join(notices)}]")
    return output


async def grep(
    ctx: RunContext[WorkspaceDeps],
    pattern: Annotated[str, Field(min_length=1)],
    path: Annotated[str | None, Field(min_length=1)] = None,
    glob: Annotated[str | None, Field(min_length=1)] = None,
    ignore_case: bool = False,
    literal: bool = False,
    limit: Annotated[int, Field(ge=1)] = GREP_MAX_MATCHES,
) -> str:
    """Search UTF-8 text files for matching lines with ripgrep.

    Args:
        pattern: Pattern to search for as a regex or literal string.
        path: Optional file or directory to search, relative to the workspace
            root or absolute.
        glob: Optional glob filter such as '*.py' or 'src/**/*.ts'.
        ignore_case: Whether to search case-insensitively.
        literal: Whether to treat pattern as a literal string instead of a
            regex.
        limit: Maximum number of matches to return before grep's own output
            ceiling is applied.
    """
    if os.name == "nt":
        ensure_windows_search_tool("rg", silent=True)

    filesystem_policy = await approved_read_only_filesystem_policy(
        ctx=ctx,
        tool_path=path,
        action="grep",
    )
    result = await _execute_grep_async(
        read_only_worker=ctx.deps.read_only_worker,
        workspace_root=ctx.deps.workspace_root,
        filesystem_policy=filesystem_policy,
        pattern=pattern,
        path=path,
        glob=glob,
        ignore_case=ignore_case,
        literal=literal,
        limit=limit,
    )
    return make_tool_return(
        return_value=result,
        title=f"grep {truncate_activity_label(pattern)}",
        summary="search completed",
        details=GrepActivityDetails(
            pattern=pattern,
            path=path,
            short_path=shorten_path(path, str(ctx.deps.workspace_root)),
            glob=glob,
            ignore_case=ignore_case,
            literal=literal,
            limit=limit,
        ),
    )


GREP_TOOL = Tool(
    grep,
    takes_ctx=True,
    name="grep",
    description=(
        "Search UTF-8 text files for a pattern using ripgrep. Returns "
        "matching lines with relative file paths and line numbers. Respects "
        ".gitignore and bounds output to 100 matches or 50 KiB."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=False,
)

__all__ = ["GREP_MAX_BYTES", "GREP_MAX_MATCHES", "GREP_TOOL", "grep"]
