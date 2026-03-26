from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    WriteToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)


def execute_write(*, tool_input: WriteToolInput, workspace_root: Path | str) -> str:
    path = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=tool_input.path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tool_input.content.encode("utf-8"))
    return f"Wrote {path}"


def create_write_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def write(path: str, content: str) -> str | dict[str, bool | str]:
        """Write a UTF-8 text file, creating parent directories as needed."""

        try:
            return execute_write(
                tool_input=WriteToolInput(path=path, content=content),
                workspace_root=root,
            )
        except (OSError, UnicodeError) as error:
            return make_tool_error_result(error)

    return Tool(write, name="write", strict=True)

__all__ = ["create_write_tool", "execute_write"]
