from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import LsActivityDetails
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
    LsCallResult,
    LsWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
)

LS_MAX_BYTES = 50 * 1024
LS_DEFAULT_LIMIT = 500


async def _execute_ls_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
    filesystem_policy,
    path: str | None = None,
    limit: int = LS_DEFAULT_LIMIT,
) -> str:
    response = await read_only_worker.send(
        LsWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(workspace_root),
            filesystem_policy=filesystem_policy,
            path=path,
            limit=limit,
            max_bytes=LS_MAX_BYTES,
        )
    )
    if not isinstance(response, LsCallResult):
        raise RuntimeError(
            "Read-only worker returned the wrong response type for ls: "
            f"{type(response).__name__}"
        )
    return _render_ls_call_result(response)


def _render_ls_call_result(result: LsCallResult) -> str:
    if not result.entries:
        return "(empty directory)"

    output = "\n".join(
        f"{entry.name}/" if entry.is_dir else entry.name for entry in result.entries
    )
    notices: list[str] = []
    if result.limit_hit:
        notices.append(
            "Showing first "
            f"{len(result.entries)} entries. Use limit={len(result.entries) * 2} "
            "for more."
        )
    if result.byte_limit_hit:
        notices.append(
            f"Listing exceeded {LS_MAX_BYTES} bytes. Narrow the path or use "
            "a smaller limit."
        )
    if notices:
        output = append_tool_note(output, f"[{' '.join(notices)}]")
    return output

async def ls(
    ctx: RunContext[WorkspaceDeps],
    path: Annotated[str | None, Field(min_length=1)] = None,
    limit: Annotated[int, Field(ge=1)] = LS_DEFAULT_LIMIT,
) -> str:
    """List directory contents in a bounded, sorted view.

    Args:
        path: Optional directory to list, relative to the workspace root or
            absolute.
        limit: Maximum number of entries to return before ls's own byte
            ceiling is applied.
    """

    filesystem_policy = await approved_read_only_filesystem_policy(
        ctx=ctx,
        tool_path=path,
        action="ls",
    )
    result = await _execute_ls_async(
        read_only_worker=ctx.deps.read_only_worker,
        workspace_root=ctx.deps.workspace_root,
        filesystem_policy=filesystem_policy,
        path=path,
        limit=limit,
    )
    title = "ls ." if path is None else f"ls {truncate_activity_label(path)}"
    return make_tool_return(
        return_value=result,
        title=title,
        summary="listing completed",
        details=LsActivityDetails(
            path=path,
            short_path=shorten_path(path, str(ctx.deps.workspace_root)),
            limit=limit,
        ),
    )


LS_TOOL = Tool(
    ls,
    takes_ctx=True,
    name="ls",
    description=(
        "List directory contents in alphabetical order. Includes dotfiles "
        "and adds '/' suffixes for directories. Output is bounded to 500 "
        "entries or 50 KiB."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=False,
)

__all__ = ["LS_DEFAULT_LIMIT", "LS_MAX_BYTES", "LS_TOOL", "ls"]
