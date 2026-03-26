from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import ReadToolInput
from pi_code_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)


def execute_read(*, tool_input: ReadToolInput, workspace_root: Path | str) -> str:
    path = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=tool_input.path,
    )
    return path.read_text(encoding="utf-8")


def create_read_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def read(path: str) -> str:
        """Read a UTF-8 text file and return its full contents."""

        return execute_read(tool_input=ReadToolInput(path=path), workspace_root=root)

    return Tool(read, name="read", strict=True)


__all__ = ["create_read_tool", "execute_read"]
