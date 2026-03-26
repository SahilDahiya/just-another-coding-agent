from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import EditToolInput


def execute_edit(tool_input: EditToolInput) -> str:
    path = Path(tool_input.path)
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


def edit(path: str, old_text: str, new_text: str) -> str:
    """Replace one exact text match in a UTF-8 text file."""

    return execute_edit(
        EditToolInput(
            path=path,
            old_text=old_text,
            new_text=new_text,
        )
    )


EDIT_TOOL = Tool(edit, name="edit", strict=True)

__all__ = ["EDIT_TOOL", "edit", "execute_edit"]
