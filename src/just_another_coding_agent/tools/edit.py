from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import EditToolInput
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)


def execute_edit(*, tool_input: EditToolInput, workspace_root: Path | str) -> str:
    path = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=tool_input.path,
    )
    content = path.read_bytes().decode("utf-8")
    occurrences = content.count(tool_input.old_text)

    if occurrences != 1:
        raise ValueError(
            "old_text must match exactly once in "
            f"{path}; found {occurrences} occurrences"
        )

    updated = content.replace(tool_input.old_text, tool_input.new_text, 1)
    if updated == content:
        raise ValueError(f"Edit would not change file contents: {path}")

    path.write_bytes(updated.encode("utf-8"))
    return f"Edited {path}"


def create_edit_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def edit(path: str, old_text: str, new_text: str) -> str:
        """Replace one exact text match in a UTF-8 text file."""

        return execute_edit(
            tool_input=EditToolInput(
                path=path,
                old_text=old_text,
                new_text=new_text,
            ),
            workspace_root=root,
        )

    return Tool(edit, name="edit", strict=True)

__all__ = ["create_edit_tool", "execute_edit"]
