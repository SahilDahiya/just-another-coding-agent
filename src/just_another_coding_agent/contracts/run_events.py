from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from just_another_coding_agent.contracts.compaction import CompactionBudgetReport
from just_another_coding_agent.contracts.platform import ShellFamily

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class _RunEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str


class _ToolActivityDetailsBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str


class ShellActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["shell"] = "shell"
    command_preview: str
    shell_family: ShellFamily
    timeout: int | None = None
    exit_code: int | None = None


class ReadActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["read"] = "read"
    path: str
    short_path: str | None = None
    offset: int | None = None
    limit: int | None = None


class WriteActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["write"] = "write"
    path: str
    bytes_written: int | None = None


class EditActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["edit"] = "edit"
    path: str
    diff: str | None = None
    added_lines: int | None = None
    removed_lines: int | None = None


class GrepActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["grep"] = "grep"
    pattern: str
    path: str | None = None
    short_path: str | None = None
    glob: str | None = None
    ignore_case: bool = False
    literal: bool = False
    limit: int | None = None


class LsActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["ls"] = "ls"
    path: str | None = None
    short_path: str | None = None
    limit: int | None = None


class FindActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["find"] = "find"
    pattern: str
    path: str | None = None
    short_path: str | None = None
    limit: int | None = None


ToolActivityDetails = Annotated[
    ShellActivityDetails
    | ReadActivityDetails
    | WriteActivityDetails
    | EditActivityDetails
    | GrepActivityDetails
    | LsActivityDetails
    | FindActivityDetails,
    Field(discriminator="kind"),
]


class ToolActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    display_label: str | None = None
    summary: str | None = None
    duration_ms: int | None = None
    details: ToolActivityDetails | None = None
    group_kind: str | None = None


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
    activity: ToolActivity | None = None


class ToolCallSucceededEvent(_RunEventBase):
    type: Literal["tool_call_succeeded"] = "tool_call_succeeded"
    tool_call_id: str
    tool_name: str
    result: JsonValue | None
    activity: ToolActivity | None = None


class ToolCallUpdatedEvent(_RunEventBase):
    type: Literal["tool_call_updated"] = "tool_call_updated"
    tool_call_id: str
    tool_name: str
    partial_result: JsonValue | None
    activity: ToolActivity | None = None


class ToolCallFailedEvent(_RunEventBase):
    type: Literal["tool_call_failed"] = "tool_call_failed"
    tool_call_id: str
    tool_name: str
    error_type: str
    message: str
    activity: ToolActivity | None = None


class RunSucceededEvent(_RunEventBase):
    type: Literal["run_succeeded"] = "run_succeeded"
    output_text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    context_window_used: float | None = None
    next_request_context_window_used: float | None = None


class RunFailedEvent(_RunEventBase):
    type: Literal["run_failed"] = "run_failed"
    error_type: str
    message: str


class SessionCompactionStartedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_compaction_started"] = "session_compaction_started"
    budget: CompactionBudgetReport


class SessionCompactionCompletedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_compaction_completed"] = "session_compaction_completed"
    compaction_id: str
    compacted_through_run_id: str
    budget_before: CompactionBudgetReport
    budget_after: CompactionBudgetReport
    estimated_tokens_saved: int
    estimated_percent_saved: float
    estimated_headroom_gain_tokens: int | None = None


class SessionCompactionWarningEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_compaction_warning"] = "session_compaction_warning"
    compaction_count: int
    message: str


class SessionTurnContextStatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_turn_context_status"] = "session_turn_context_status"
    status: Literal["missing", "reused", "cleared"]
    reason: str
    persisted_run_id: str | None = None


SessionLifecycleEvent = (
    SessionCompactionStartedEvent
    | SessionCompactionCompletedEvent
    | SessionCompactionWarningEvent
    | SessionTurnContextStatusEvent
)


RunEvent = Annotated[
    RunStartedEvent
    | AssistantTextDeltaEvent
    | ToolCallStartedEvent
    | ToolCallUpdatedEvent
    | ToolCallSucceededEvent
    | ToolCallFailedEvent
    | RunSucceededEvent
    | RunFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "AssistantTextDeltaEvent",
    "EditActivityDetails",
    "FindActivityDetails",
    "GrepActivityDetails",
    "JsonValue",
    "LsActivityDetails",
    "ReadActivityDetails",
    "RunEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "RunSucceededEvent",
    "SessionCompactionCompletedEvent",
    "SessionCompactionStartedEvent",
    "SessionCompactionWarningEvent",
    "SessionLifecycleEvent",
    "SessionTurnContextStatusEvent",
    "ShellActivityDetails",
    "ToolActivity",
    "ToolActivityDetails",
    "ToolCallFailedEvent",
    "ToolCallStartedEvent",
    "ToolCallUpdatedEvent",
    "ToolCallSucceededEvent",
    "WriteActivityDetails",
]
