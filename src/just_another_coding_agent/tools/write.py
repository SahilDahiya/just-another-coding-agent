from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import WriteActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools._permissions import (
    maybe_request_file_write_approval,
)
from just_another_coding_agent.tools._safe_fs import write_bytes_no_symlink
from just_another_coding_agent.tools._workspace import (
    absolutize_workspace_path,
    resolve_workspace_path,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import reraise_path_error


def execute_write(
    *,
    workspace_root: Path | str,
    path: str,
    content: str,
) -> str:
    try:
        resolved_path = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path,
        )
        absolute_path = absolutize_workspace_path(
            workspace_root=workspace_root,
            tool_path=path,
        )
        write_bytes_no_symlink(
            absolute_path,
            content.encode("utf-8"),
        )
    except OSError as error:
        reraise_path_error(error)
    return f"Wrote {resolved_path}"


async def write(
    ctx: RunContext[WorkspaceDeps],
    path: Annotated[str, Field(min_length=1)],
    content: str,
) -> str:
    """Create or overwrite a UTF-8 text file.

    Args:
        path: Path to the file to write, relative to the workspace root or absolute.
        content: Full UTF-8 file contents to write.
    """
    await maybe_request_file_write_approval(
        ctx=ctx,
        tool_path=path,
        action="write",
    )

    result = execute_write(
        workspace_root=ctx.deps.workspace_root,
        path=path,
        content=content,
    )
    return make_tool_return(
        return_value=result,
        title=f"write {truncate_activity_label(path)}",
        summary="wrote file",
        details=WriteActivityDetails(
            path=path,
            bytes_written=len(content.encode("utf-8")),
        ),
    )


WRITE_TOOL = Tool(
    write,
    takes_ctx=True,
    name="write",
    description=(
        "Create or overwrite an entire UTF-8 text file. Creates parent "
        "directories automatically. Use write for new files or complete "
        "rewrites."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

__all__ = ["WRITE_TOOL", "execute_write", "write"]
