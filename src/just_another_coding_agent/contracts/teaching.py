from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _TeachingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TeachingRelationship(_TeachingModel):
    statement: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_statement(self) -> "TeachingRelationship":
        if self.statement.strip() == "":
            raise ValueError("Teaching relationship statement must not be blank")
        return self


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


class TeachingPacket(_TeachingModel):
    title: Annotated[str, Field(min_length=1)]
    concept: Annotated[str, Field(min_length=1)]
    relationships: list[TeachingRelationship] = Field(min_length=1)
    snippets: list[TeachingSnippet] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def _validate_contents(self) -> "TeachingPacket":
        if self.title.strip() == "":
            raise ValueError("Teaching packet title must not be blank")
        if self.concept.strip() == "":
            raise ValueError("Teaching packet concept must not be blank")
        return self


__all__ = [
    "TeachingPacket",
    "TeachingRelationship",
    "TeachingSnippet",
    "TeachingSnippetRef",
]
