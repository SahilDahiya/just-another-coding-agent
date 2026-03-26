from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import WriteToolInput


def execute_write(tool_input: WriteToolInput) -> str:
    path = Path(tool_input.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tool_input.content.encode("utf-8"))
    return f"Wrote {path}"


def write(path: str, content: str) -> str:
    """Write a UTF-8 text file, creating parent directories as needed."""

    return execute_write(WriteToolInput(path=path, content=content))


WRITE_TOOL = Tool(write, name="write", strict=True)

__all__ = ["WRITE_TOOL", "execute_write", "write"]
