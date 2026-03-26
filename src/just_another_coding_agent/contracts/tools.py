from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_TOOL_NAMES = ("read", "write", "edit", "bash")
CanonicalToolName = Literal["read", "write", "edit", "bash"]


class ReadToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    offset: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)


class WriteToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    content: str


class EditToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str


class BashToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    command: str = Field(min_length=1)
    timeout: int | None = Field(default=None, gt=0)


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
    "BashToolInput",
    "CANONICAL_TOOL_NAMES",
    "CanonicalToolName",
    "EditToolInput",
    "ReadToolInput",
    "ToolErrorResult",
    "WriteToolInput",
    "make_tool_error_result",
]
