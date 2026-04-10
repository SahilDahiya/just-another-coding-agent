from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CANONICAL_TOOL_NAMES = (
    "read",
    "write",
    "edit",
    "shell",
    "grep",
    "ls",
    "find",
    "subagent",
)
CanonicalToolName = Literal[
    "read",
    "write",
    "edit",
    "shell",
    "grep",
    "ls",
    "find",
    "subagent",
]


class ToolErrorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ok: Literal[False] = False
    error_type: str
    message: str


def make_tool_error_result(error: Exception) -> dict[str, bool | str]:
    return ToolErrorResult(
        error_type=type(error).__name__,
        message=str(error),
    ).model_dump(mode="json")


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "CanonicalToolName",
    "ToolErrorResult",
    "make_tool_error_result",
]
