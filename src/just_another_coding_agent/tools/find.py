from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import FindActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    shorten_path,
    truncate_activity_label,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.read_only_worker.protocol import (
    FindCallResult,
    FindWorkerRequest,
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

FIND_DEFAULT_LIMIT = 1000
FIND_MAX_BYTES = 50 * 1024


async def _execute_find_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
    pattern: str,
    path: str | None = None,
    limit: int = FIND_DEFAULT_LIMIT,
) -> str:
    response = await read_only_worker.send(
        FindWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(workspace_root),
            pattern=pattern,
            path=path,
            limit=limit,
            max_bytes=FIND_MAX_BYTES,
        )
    )
    if not isinstance(response, FindCallResult):
        raise RuntimeError(
            "Read-only worker returned the wrong response type for find: "
            f"{type(response).__name__}"
        )
    return _render_find_call_result(response)


def _render_find_call_result(result: FindCallResult) -> str:
    if not result.matches:
        return "No files found matching pattern."

    output = "\n".join(result.matches)
    notices: list[str] = []
    if result.limit_hit:
        notices.append(
            "Showing first "
            f"{len(result.matches)} results. Use limit={len(result.matches) * 2} "
            "for more or refine the pattern."
        )
    if result.byte_limit_hit:
        notices.append(
            f"Find output exceeded {FIND_MAX_BYTES} bytes. Refine the pattern or path."
        )
    if notices:
        output = append_tool_note(output, f"[{' '.join(notices)}]")
    return output

async def find(
    ctx: RunContext[WorkspaceDeps],
    pattern: Annotated[str, Field(min_length=1)],
    path: Annotated[str | None, Field(min_length=1)] = None,
    limit: Annotated[int, Field(ge=1)] = FIND_DEFAULT_LIMIT,
) -> str:
    """Find files by glob pattern with ripgrep-backed file discovery.

    Args:
        pattern: Glob pattern to match, such as '*.py' or 'src/**/*.ts'.
        path: Optional directory to search, relative to the workspace root or
            absolute.
        limit: Maximum number of results to return before find's own byte
            ceiling is applied.
    """
    ensure_windows_search_tool("rg", silent=True)

    result = await _execute_find_async(
        read_only_worker=ctx.deps.read_only_worker,
        workspace_root=ctx.deps.workspace_root,
        pattern=pattern,
        path=path,
        limit=limit,
    )
    return make_tool_return(
        return_value=result,
        title=f"find {truncate_activity_label(pattern)}",
        summary="find completed",
        details=FindActivityDetails(
            pattern=pattern,
            path=path,
            short_path=shorten_path(path, str(ctx.deps.workspace_root)),
            limit=limit,
        ),
    )


FIND_TOOL = Tool(
    find,
    takes_ctx=True,
    name="find",
    description=(
        "Find files by glob pattern using ripgrep-backed file discovery. "
        "Returns paths relative to the searched directory, respects "
        ".gitignore, and bounds output to 1000 results or 50 KiB."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=False,
)


__all__ = [
    "FIND_DEFAULT_LIMIT",
    "FIND_MAX_BYTES",
    "find",
    "FIND_TOOL",
]
