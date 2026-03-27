from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    GrepToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)

GREP_MAX_MATCHES = 100
GREP_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_CHARS = 300


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


def _append_grep_note(text: str, note: str) -> str:
    if not text:
        return note
    return f"{text}\n\n{note}"


def _resolve_match_path(*, path_text: str, workspace_root: Path) -> Path:
    match_path = Path(path_text)
    if match_path.is_absolute():
        return match_path.resolve()
    return (workspace_root / match_path).resolve()


def execute_grep(*, tool_input: GrepToolInput, workspace_root: Path | str) -> str:
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

    args = [
        rg_path,
        "--json",
        "--line-number",
        "--sort",
        "path",
        "--color=never",
        "--hidden",
    ]
    if tool_input.ignore_case:
        args.append("--ignore-case")
    if tool_input.literal:
        args.append("--fixed-strings")
    if tool_input.glob is not None:
        args.extend(["--glob", tool_input.glob])
    args.extend([tool_input.pattern, str(search_path)])

    matches: list[str] = []
    output_bytes = 0
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
            formatted_bytes = len(formatted.encode("utf-8"))

            if len(matches) >= min(tool_input.limit, GREP_MAX_MATCHES):
                limit_hit = True
                process.terminate()
                break

            if output_bytes + formatted_bytes > GREP_MAX_BYTES:
                byte_limit_hit = True
                process.terminate()
                break

            matches.append(formatted)
            output_bytes += formatted_bytes + 1

        stderr_output = process.stderr.read().strip()
        return_code = process.wait()

    if return_code not in (0, 1) and not (limit_hit or byte_limit_hit):
        raise ValueError(
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

    result = "\n".join(matches)
    notices: list[str] = []
    if limit_hit:
        notices.append(
            f"Showing first {min(tool_input.limit, GREP_MAX_MATCHES)} matches. "
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
        result = _append_grep_note(result, f"[{' '.join(notices)}]")
    return result


def create_grep_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def grep(
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        ignore_case: bool = False,
        literal: bool = False,
        limit: int = GREP_MAX_MATCHES,
    ) -> str | dict[str, bool | str]:
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

        try:
            return execute_grep(
                tool_input=GrepToolInput(
                    pattern=pattern,
                    path=path,
                    glob=glob,
                    ignore_case=ignore_case,
                    literal=literal,
                    limit=limit,
                ),
                workspace_root=root,
            )
        except (
            OSError,
            UnicodeError,
            ValueError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
        ) as error:
            return make_tool_error_result(error)

    return Tool(
        grep,
        name="grep",
        description=(
            "Search UTF-8 text files for a pattern using ripgrep. Returns "
            "matching lines with relative file paths and line numbers. Respects "
            ".gitignore and bounds output to 100 matches or 50 KiB."
        ),
        docstring_format="google",
        require_parameter_descriptions=True,
        strict=True,
    )


__all__ = ["GREP_MAX_BYTES", "GREP_MAX_MATCHES", "create_grep_tool", "execute_grep"]
