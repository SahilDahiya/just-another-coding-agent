from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _OnboardingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OnboardingQuestionRequest(_OnboardingModel):
    attempt_id: str
    question_type: Literal["mcq"] = "mcq"
    prompt: str
    options: list[str] = Field(min_length=4, max_length=4)
    evidence: list[str] = Field(default_factory=list)

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
        for evidence_path in self.evidence:
            if evidence_path.strip() == "":
                raise ValueError("Onboarding evidence path must not be blank")
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

