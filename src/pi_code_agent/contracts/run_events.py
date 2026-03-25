from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _RunEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str


class RunStartedEvent(_RunEventBase):
    type: Literal["run_started"] = "run_started"


class AssistantTextDeltaEvent(_RunEventBase):
    type: Literal["assistant_text_delta"] = "assistant_text_delta"
    delta: str


class RunSucceededEvent(_RunEventBase):
    type: Literal["run_succeeded"] = "run_succeeded"
    output_text: str


class RunFailedEvent(_RunEventBase):
    type: Literal["run_failed"] = "run_failed"
    error_type: str
    message: str


RunEvent = Annotated[
    RunStartedEvent | AssistantTextDeltaEvent | RunSucceededEvent | RunFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "AssistantTextDeltaEvent",
    "RunEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "RunSucceededEvent",
]
