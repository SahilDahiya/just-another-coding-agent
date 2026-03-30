from __future__ import annotations

import json
import shutil
import subprocess
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
from just_another_coding_agent.tools._workspace import resolve_workspace_path
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    reraise_path_error,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    GrepCallResult,
    GrepWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    collect_bounded_items,
)

GREP_MAX_MATCHES = 100
GREP_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_CHARS = 300


async def _execute_grep_async(
    *,
    read_only_worker: ReadOnlyWorkerRuntime,
    workspace_root: Path | str,
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


def _format_match_path(*, file_path: Path, workspace_root: Path) -> str:
    try:
        return file_path.relative_to(workspace_root).as_posix()
    except ValueError:
        return str(file_path)


def _truncate_match_text(text: str) -> str:
    stripped = text.rstrip("\r\n")
    if len(stripped) <= GREP_MAX_LINE_CHARS:
        return stripped
    return f"{stripped[:GREP_MAX_LINE_CHARS]}..."


def _resolve_match_path(*, path_text: str, workspace_root: Path) -> Path:
    match_path = Path(path_text)
    if match_path.is_absolute():
        return match_path.resolve()
    return (workspace_root / match_path).resolve()


def execute_grep(
    *,
    workspace_root: Path | str,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    limit: int = GREP_MAX_MATCHES,
) -> str:
    root = Path(workspace_root)
    rg_path = shutil.which("rg")
    if rg_path is None:
        raise ToolCommandError("ripgrep (rg) is not installed")

    try:
        search_path = resolve_workspace_path(
            workspace_root=root,
            tool_path=path or ".",
        )
        if not search_path.exists():
            raise FileNotFoundError(search_path)
    except OSError as error:
        reraise_path_error(error)

    args = [
        rg_path,
        "--json",
        "--line-number",
        "--sort",
        "path",
        "--color=never",
        "--hidden",
    ]
    if ignore_case:
        args.append("--ignore-case")
    if literal:
        args.append("--fixed-strings")
    if glob is not None:
        args.extend(["--glob", glob])
    args.extend([pattern, str(search_path)])

    matches: list[str] = []
    limit_hit = False
    byte_limit_hit = False
    truncated_lines = False

    with subprocess.Popen(
        args,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        assert process.stdout is not None
        assert process.stderr is not None

        for raw_line in process.stdout:
            event = json.loads(raw_line)
            if event.get("type") != "match":
                continue

            data = event["data"]
            raw_match_text = data["lines"]["text"]
            match_text = _truncate_match_text(raw_match_text)
            if match_text != raw_match_text.rstrip("\r\n"):
                truncated_lines = True

            match_path = _resolve_match_path(
                path_text=data["path"]["text"],
                workspace_root=root,
            )
            formatted = (
                f"{_format_match_path(file_path=match_path, workspace_root=root)}:"
                f"{data['line_number']}:{match_text}"
            )
            matches.append(formatted)

        stderr_output = process.stderr.read().strip()
        return_code = process.wait()

    if return_code not in (0, 1) and not (limit_hit or byte_limit_hit):
        raise ToolCommandError(
            stderr_output or f"ripgrep failed with exit code {return_code}"
        )

    if not matches:
        if return_code == 1:
            return "No matches found."
        if byte_limit_hit:
            return (
                f"[Search output exceeded {GREP_MAX_BYTES} bytes before a full "
                "match could be returned. Narrow the pattern or path.]"
            )
        return "No matches found."

    bounded = collect_bounded_items(
        matches,
        item_limit=min(limit, GREP_MAX_MATCHES),
        max_bytes=GREP_MAX_BYTES,
    )
    limit_hit = bounded.limit_hit
    byte_limit_hit = bounded.byte_limit_hit
    result = "\n".join(bounded.items)
    notices: list[str] = []
    if limit_hit:
        notices.append(
            f"Showing first {min(limit, GREP_MAX_MATCHES)} matches. "
            "Refine pattern or path to narrow results."
        )
    if byte_limit_hit:
        notices.append(
            f"Search output exceeded {GREP_MAX_BYTES} bytes. Refine pattern or path."
        )
    if truncated_lines:
        notices.append(
            f"Some match lines were truncated to {GREP_MAX_LINE_CHARS} characters."
        )
    if notices:
        result = append_tool_note(result, f"[{' '.join(notices)}]")
    return result


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

    result = await _execute_grep_async(
        read_only_worker=ctx.deps.read_only_worker,
        workspace_root=ctx.deps.workspace_root,
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


__all__ = ["GREP_MAX_BYTES", "GREP_MAX_MATCHES", "GREP_TOOL", "execute_grep", "grep"]
