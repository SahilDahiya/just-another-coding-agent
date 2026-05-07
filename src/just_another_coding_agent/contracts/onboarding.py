from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _OnboardingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OnboardingCodeSnippet(_OnboardingModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str

    @model_validator(mode="after")
    def _validate_contents(self) -> "OnboardingCodeSnippet":
        if self.path.strip() == "":
            raise ValueError("Onboarding snippet path must not be blank")
        if self.end_line < self.start_line:
            raise ValueError("Onboarding snippet line span is invalid")
        if self.text.strip() == "":
            raise ValueError("Onboarding snippet text must not be blank")
        return self


class OnboardingQuestionRequest(_OnboardingModel):
    attempt_id: str
    question_type: Literal["mcq"] = "mcq"
    prompt: str
    options: list[str] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _validate_contents(self) -> "OnboardingQuestionRequest":
        if self.prompt.strip() == "":
            raise ValueError("Onboarding question prompt must not be blank")
        normalized_options: list[str] = []
        for option in self.options:
            if option.strip() == "":
                raise ValueError("Onboarding option must not be blank")
            normalized_options.append(option.strip().lower())
        if len(set(normalized_options)) != len(normalized_options):
            raise ValueError("Onboarding options must be unique")
        return self


class OnboardingAnswerResult(_OnboardingModel):
    session_id: str
    attempt_id: str
    question_type: Literal["mcq"] = "mcq"
    selected_index: int
    correct_index: int
    correct_option: str
    is_correct: bool
    explanation: str
