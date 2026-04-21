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


class ToolDeniedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ok: Literal[False] = False
    outcome: Literal["denied"] = "denied"
    denial_type: str
    message: str


def make_tool_error_result(error: Exception) -> dict[str, bool | str]:
    return ToolErrorResult(
        error_type=type(error).__name__,
        message=str(error),
    ).model_dump(mode="json")


def make_tool_denied_result(
    *,
    message: str,
    denial_type: str = "approval_denied",
) -> dict[str, bool | str]:
    return ToolDeniedResult(
        denial_type=denial_type,
        message=message,
    ).model_dump(mode="json")


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "CanonicalToolName",
    "ToolDeniedResult",
    "ToolErrorResult",
    "make_tool_denied_result",
    "make_tool_error_result",
]
