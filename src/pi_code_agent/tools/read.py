from __future__ import annotations

from pathlib import Path

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import ReadToolInput


def execute_read(tool_input: ReadToolInput) -> str:
    return Path(tool_input.path).read_text(encoding="utf-8")


def read(path: str) -> str:
    """Read a UTF-8 text file and return its full contents.

    Args:
        path: Path to an existing UTF-8 text file.
    """

    return execute_read(ReadToolInput(path=path))


READ_TOOL = Tool(read, name="read", strict=True)

__all__ = ["READ_TOOL", "execute_read", "read"]
