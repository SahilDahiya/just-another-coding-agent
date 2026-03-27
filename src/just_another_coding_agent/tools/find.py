from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    FindToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)

FIND_DEFAULT_LIMIT = 1000
FIND_MAX_BYTES = 50 * 1024


def _append_find_note(text: str, note: str) -> str:
    if not text:
        return note
    return f"{text}\n\n{note}"


def _matches_pattern(path_text: str, pattern: str) -> bool:
    path = PurePosixPath(path_text)
    if path.match(pattern):
        return True
    if pattern.startswith("**/"):
        return path.match(pattern.removeprefix("**/"))
    return False


def _normalize_rg_path(path_text: str) -> str:
    return path_text.removeprefix("./")


def execute_find(*, tool_input: FindToolInput, workspace_root: Path | str) -> str:
    root = normalize_workspace_root(workspace_root)
    rg_path = shutil.which("rg")
    if rg_path is None:
        raise FileNotFoundError("ripgrep (rg) is not installed")

    search_path = resolve_workspace_path(
        workspace_root=root,
        tool_path=tool_input.path or ".",
    )
    if not search_path.exists():
        raise FileNotFoundError(search_path)
    if not search_path.is_dir():
        raise NotADirectoryError(search_path)

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
        raise RuntimeError(
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
            if _matches_pattern(path_text, tool_input.pattern)
        ],
        key=str.lower,
    )
    if not matches:
        return "No files found matching pattern."

    displayed_paths: list[str] = []
    output_bytes = 0
    limit_hit = False
    byte_limit_hit = False

    for path_text in matches:
        if len(displayed_paths) >= tool_input.limit:
            limit_hit = True
            break

        path_bytes = len(path_text.encode("utf-8"))
        if output_bytes + path_bytes + 1 > FIND_MAX_BYTES:
            byte_limit_hit = True
            break

        displayed_paths.append(path_text)
        output_bytes += path_bytes + 1

    result = "\n".join(displayed_paths)
    notices: list[str] = []
    if limit_hit:
        notices.append(
            "Showing first "
            f"{tool_input.limit} results. Use limit={tool_input.limit * 2} "
            "for more or refine the pattern."
        )
    if byte_limit_hit:
        notices.append(
            f"Find output exceeded {FIND_MAX_BYTES} bytes. Refine the pattern or path."
        )
    if notices:
        result = _append_find_note(result, f"[{' '.join(notices)}]")

    return result


def create_find_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def find(
        pattern: str,
        path: str | None = None,
        limit: int = FIND_DEFAULT_LIMIT,
    ) -> str | dict[str, bool | str]:
        """Find files by glob pattern with ripgrep-backed file discovery.

        Args:
            pattern: Glob pattern to match, such as '*.py' or 'src/**/*.ts'.
            path: Optional directory to search, relative to the workspace root or
                absolute.
            limit: Maximum number of results to return before find's own byte
                ceiling is applied.
        """

        try:
            return execute_find(
                tool_input=FindToolInput(pattern=pattern, path=path, limit=limit),
                workspace_root=root,
            )
        except (
            OSError,
            UnicodeError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as error:
            return make_tool_error_result(error)

    return Tool(
        find,
        name="find",
        description=(
            "Find files by glob pattern using ripgrep-backed file discovery. "
            "Returns paths relative to the searched directory, respects "
            ".gitignore, and bounds output to 1000 results or 50 KiB."
        ),
        docstring_format="google",
        require_parameter_descriptions=True,
        strict=True,
    )


__all__ = [
    "FIND_DEFAULT_LIMIT",
    "FIND_MAX_BYTES",
    "create_find_tool",
    "execute_find",
]
