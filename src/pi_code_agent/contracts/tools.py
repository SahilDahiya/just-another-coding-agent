from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_TOOL_NAMES = ("read", "write", "edit", "bash")
CanonicalToolName = Literal["read", "write", "edit", "bash"]


class ReadToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)


class WriteToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    content: str


class EditToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "CanonicalToolName",
    "EditToolInput",
    "ReadToolInput",
    "WriteToolInput",
]
