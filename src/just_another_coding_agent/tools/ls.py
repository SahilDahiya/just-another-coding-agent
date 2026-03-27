from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    LsToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)

LS_MAX_BYTES = 50 * 1024
LS_DEFAULT_LIMIT = 500


def _append_ls_note(text: str, note: str) -> str:
    if not text:
        return note
    return f"{text}\n\n{note}"


def _format_entry(entry: Path) -> str:
    return f"{entry.name}/" if entry.is_dir() else entry.name


def execute_ls(*, tool_input: LsToolInput, workspace_root: Path | str) -> str:
    root = normalize_workspace_root(workspace_root)
    directory = resolve_workspace_path(
        workspace_root=root,
        tool_path=tool_input.path or ".",
    )

    if not directory.exists():
        raise FileNotFoundError(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    entries = sorted(directory.iterdir(), key=lambda entry: entry.name.lower())
    if not entries:
        return "(empty directory)"

    formatted_entries = [_format_entry(entry) for entry in entries]
    displayed_entries: list[str] = []
    output_bytes = 0
    limit_hit = False
    byte_limit_hit = False

    for entry in formatted_entries:
        if len(displayed_entries) >= tool_input.limit:
            limit_hit = True
            break

        entry_bytes = len(entry.encode("utf-8"))
        if output_bytes + entry_bytes + 1 > LS_MAX_BYTES:
            byte_limit_hit = True
            break

        displayed_entries.append(entry)
        output_bytes += entry_bytes + 1

    result = "\n".join(displayed_entries)
    notices: list[str] = []
    if limit_hit:
        notices.append(
            "Showing first "
            f"{tool_input.limit} entries. Use limit={tool_input.limit * 2} "
            "for more."
        )
    if byte_limit_hit:
        notices.append(
            f"Listing exceeded {LS_MAX_BYTES} bytes. Narrow the path or use "
            "a smaller limit."
        )
    if notices:
        result = _append_ls_note(result, f"[{' '.join(notices)}]")

    return result


def create_ls_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def ls(
        path: str | None = None,
        limit: int = LS_DEFAULT_LIMIT,
    ) -> str | dict[str, bool | str]:
        """List directory contents in a bounded, sorted view.

        Args:
            path: Optional directory to list, relative to the workspace root or
                absolute.
            limit: Maximum number of entries to return before ls's own byte
                ceiling is applied.
        """

        try:
            return execute_ls(
                tool_input=LsToolInput(path=path, limit=limit),
                workspace_root=root,
            )
        except (OSError, UnicodeError, ValueError) as error:
            return make_tool_error_result(error)

    return Tool(
        ls,
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


__all__ = ["LS_DEFAULT_LIMIT", "LS_MAX_BYTES", "create_ls_tool", "execute_ls"]
