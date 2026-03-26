from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    EditToolInput,
    make_tool_error_result,
)
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

    def edit(path: str, old_text: str, new_text: str) -> str | dict[str, bool | str]:
        """Edit a UTF-8 text file by replacing one exact text match.

        Args:
            path: Path to the file to edit, relative to the workspace root or absolute.
            old_text: Exact existing text to replace; it must match exactly once.
            new_text: Replacement text to insert in place of old_text.
        """

        try:
            return execute_edit(
                tool_input=EditToolInput(
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                ),
                workspace_root=root,
            )
        except (OSError, UnicodeError, ValueError) as error:
            return make_tool_error_result(error)

    return Tool(
        edit,
        name="edit",
        description=(
            "Edit a UTF-8 text file by replacing exactly one occurrence of "
            "old_text with new_text. Zero or multiple matches return an error "
            "result. new_text may be empty to delete the matched text. Use "
            "this for precise surgical changes."
        ),
        docstring_format="google",
        require_parameter_descriptions=True,
        strict=True,
    )

__all__ = ["create_edit_tool", "execute_edit"]
