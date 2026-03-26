from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class _RunEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str


class RunStartedEvent(_RunEventBase):
    type: Literal["run_started"] = "run_started"


class AssistantTextDeltaEvent(_RunEventBase):
    type: Literal["assistant_text_delta"] = "assistant_text_delta"
    delta: str


class ToolCallStartedEvent(_RunEventBase):
    type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: str
    tool_name: str
    args: JsonValue | None
    args_valid: bool | None


class ToolCallSucceededEvent(_RunEventBase):
    type: Literal["tool_call_succeeded"] = "tool_call_succeeded"
    tool_call_id: str
    tool_name: str
    result: JsonValue | None


class ToolCallFailedEvent(_RunEventBase):
    type: Literal["tool_call_failed"] = "tool_call_failed"
    tool_call_id: str
    tool_name: str
    error_type: str
    message: str


class RunSucceededEvent(_RunEventBase):
    type: Literal["run_succeeded"] = "run_succeeded"
    output_text: str


class RunFailedEvent(_RunEventBase):
    type: Literal["run_failed"] = "run_failed"
    error_type: str
    message: str


RunEvent = Annotated[
    RunStartedEvent
    | AssistantTextDeltaEvent
    | ToolCallStartedEvent
    | ToolCallSucceededEvent
    | ToolCallFailedEvent
    | RunSucceededEvent
    | RunFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "AssistantTextDeltaEvent",
    "JsonValue",
    "RunEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "RunSucceededEvent",
    "ToolCallFailedEvent",
    "ToolCallStartedEvent",
    "ToolCallSucceededEvent",
]
