from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import FindActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools._subprocess_worker import (
    run_blocking_tool_in_subprocess,
)
from just_another_coding_agent.tools._workspace import resolve_workspace_path
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    reraise_path_error,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    collect_bounded_items,
)

FIND_DEFAULT_LIMIT = 1000
FIND_MAX_BYTES = 50 * 1024


async def _execute_find_async(
    *,
    workspace_root: Path | str,
    pattern: str,
    path: str | None = None,
    limit: int = FIND_DEFAULT_LIMIT,
) -> str:
    return await run_blocking_tool_in_subprocess(
        operation="find",
        kwargs={
            "workspace_root": str(workspace_root),
            "pattern": pattern,
            "path": path,
            "limit": limit,
        },
    )


def _matches_pattern(path_text: str, pattern: str) -> bool:
    path = PurePosixPath(path_text)
    if path.match(pattern):
        return True
    if pattern.startswith("**/"):
        return path.match(pattern.removeprefix("**/"))
    return False


def _normalize_rg_path(path_text: str) -> str:
    return path_text.removeprefix("./")


def execute_find(
    *,
    workspace_root: Path | str,
    pattern: str,
    path: str | None = None,
    limit: int = FIND_DEFAULT_LIMIT,
) -> str:
    rg_path = shutil.which("rg")
    if rg_path is None:
        raise ToolCommandError("ripgrep (rg) is not installed")

    try:
        search_path = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path or ".",
        )
        if not search_path.exists():
            raise FileNotFoundError(search_path)
        if not search_path.is_dir():
            raise NotADirectoryError(search_path)
    except OSError as error:
        reraise_path_error(error)

    completed = subprocess.run(
        [rg_path, "--files", "--hidden", "."],
        check=False,
        cwd=search_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise ToolCommandError(
            completed.stderr.strip()
            or f"ripgrep file listing failed with exit code {completed.returncode}"
        )

    all_paths = [
        _normalize_rg_path(line.strip())
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    matches = sorted(
        [
            path_text
            for path_text in all_paths
            if _matches_pattern(path_text, pattern)
        ],
        key=str.lower,
    )
    if not matches:
        return "No files found matching pattern."

    bounded = collect_bounded_items(
        matches,
        item_limit=limit,
        max_bytes=FIND_MAX_BYTES,
    )

    result = "\n".join(bounded.items)
    notices: list[str] = []
    if bounded.limit_hit:
        notices.append(
            "Showing first "
            f"{limit} results. Use limit={limit * 2} "
            "for more or refine the pattern."
        )
    if bounded.byte_limit_hit:
        notices.append(
            f"Find output exceeded {FIND_MAX_BYTES} bytes. Refine the pattern or path."
        )
    if notices:
        result = append_tool_note(result, f"[{' '.join(notices)}]")

    return result


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

    result = await _execute_find_async(
        workspace_root=ctx.deps.workspace_root,
        pattern=pattern,
        path=path,
        limit=limit,
    )
    return make_tool_return(
        return_value=result,
        title=f"find {truncate_activity_label(pattern)}",
        summary="find completed",
        details=FindActivityDetails(pattern=pattern, path=path, limit=limit),
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
    "execute_find",
    "find",
    "FIND_TOOL",
]
