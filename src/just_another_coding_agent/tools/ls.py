from __future__ import annotations

from pathlib import Path

from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.tools import (
    LsToolInput,
)
from just_another_coding_agent.tools._workspace import resolve_workspace_path
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import reraise_path_error
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    collect_bounded_items,
)

LS_MAX_BYTES = 50 * 1024
LS_DEFAULT_LIMIT = 500
def _format_entry(entry: Path) -> str:
    return f"{entry.name}/" if entry.is_dir() else entry.name


def execute_ls(*, tool_input: LsToolInput, workspace_root: Path | str) -> str:
    try:
        directory = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=tool_input.path or ".",
        )

        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)

        entries = sorted(directory.iterdir(), key=lambda entry: entry.name.lower())
    except OSError as error:
        reraise_path_error(error)

    if not entries:
        return "(empty directory)"

    formatted_entries = [_format_entry(entry) for entry in entries]
    bounded = collect_bounded_items(
        formatted_entries,
        item_limit=tool_input.limit,
        max_bytes=LS_MAX_BYTES,
    )

    result = "\n".join(bounded.items)
    notices: list[str] = []
    if bounded.limit_hit:
        notices.append(
            "Showing first "
            f"{tool_input.limit} entries. Use limit={tool_input.limit * 2} "
            "for more."
        )
    if bounded.byte_limit_hit:
        notices.append(
            f"Listing exceeded {LS_MAX_BYTES} bytes. Narrow the path or use "
            "a smaller limit."
        )
    if notices:
        result = append_tool_note(result, f"[{' '.join(notices)}]")

    return result


def ls(
    ctx: RunContext[WorkspaceDeps],
    path: str | None = None,
    limit: int = LS_DEFAULT_LIMIT,
) -> str:
    """List directory contents in a bounded, sorted view.

    Args:
        path: Optional directory to list, relative to the workspace root or
            absolute.
        limit: Maximum number of entries to return before ls's own byte
            ceiling is applied.
    """

    return execute_ls(
        tool_input=LsToolInput(path=path, limit=limit),
        workspace_root=ctx.deps.workspace_root,
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
)


__all__ = ["LS_DEFAULT_LIMIT", "LS_MAX_BYTES", "LS_TOOL", "execute_ls", "ls"]
