from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _TeachingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TeachingSnippetRef(_TeachingModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_contents(self) -> "TeachingSnippetRef":
        if self.path.strip() == "":
            raise ValueError("Teaching snippet path must not be blank")
        if self.end_line < self.start_line:
            raise ValueError("Teaching snippet line span is invalid")
        return self


class TeachingSnippet(_TeachingModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str

    @model_validator(mode="after")
    def _validate_contents(self) -> "TeachingSnippet":
        if self.path.strip() == "":
            raise ValueError("Teaching snippet path must not be blank")
        if self.end_line < self.start_line:
            raise ValueError("Teaching snippet line span is invalid")
        if self.text.strip() == "":
            raise ValueError("Teaching snippet text must not be blank")
        return self


__all__ = [
    "TeachingSnippet",
    "TeachingSnippetRef",
]
