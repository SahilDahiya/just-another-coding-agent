from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from just_another_coding_agent.contracts.compaction import CompactionBudgetReport
from just_another_coding_agent.contracts.onboarding import OnboardingQuestionRequest
from just_another_coding_agent.contracts.platform import ShellFamily
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalRequest,
)
from just_another_coding_agent.contracts.teaching import (
    TeachingRelationship,
    TeachingSnippet,
)

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


class SubagentActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["subagent"] = "subagent"
    name: str
    role: Literal["general", "explore", "verification"]
    spawn_mode: Literal["fresh", "fork"]
    capability: Literal["default", "shell"]
    preview_lines: list[str] = Field(default_factory=list)
    preview_terminal: bool = False


class CodeModeActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["code_mode"] = "code_mode"
    cell_id: str
    nested_tool: str
    nested_status: Literal["started", "succeeded", "failed"]
    title: str
    elapsed_ms: int | None = None
    error_type: str | None = None
    message: str | None = None


class TeachingPacketActivityDetails(_ToolActivityDetailsBase):
    kind: Literal["teaching_packet"] = "teaching_packet"
    concept: str
    relationships: list[TeachingRelationship] = Field(min_length=1)
    snippets: list[TeachingSnippet] = Field(min_length=2, max_length=5)


ToolActivityDetails = Annotated[
    ShellActivityDetails
    | ReadActivityDetails
    | WriteActivityDetails
    | EditActivityDetails
    | GrepActivityDetails
    | LsActivityDetails
    | FindActivityDetails
    | SubagentActivityDetails
    | CodeModeActivityDetails
    | TeachingPacketActivityDetails,
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


class ActivityGroupCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    read: int = 0
    search: int = 0
    list: int = 0
    shell: int = 0
    write: int = 0
    edit: int = 0
    tool: int = 0


class ActivityGroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_kind: Literal["exploration", "execution", "editing", "compaction", "other"]
    group_label: str
    group_counts: ActivityGroupCounts = Field(default_factory=ActivityGroupCounts)
    display_hint: str | None = None
    outcome: Literal["success", "denied", "operational_miss", "error"]
    elapsed_ms: int | None = None


class RunTranscriptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    elapsed_ms: int
    tool_call_count: int = 0
    tool_duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    context_window_used: float | None = None
    next_request_context_window_used: float | None = None
    had_work_activity: bool = False
    should_show_separator: bool = False
    activity_groups: list[ActivityGroupSummary] = Field(default_factory=list)


class RunStartedEvent(_RunEventBase):
    type: Literal["run_started"] = "run_started"


class AssistantTextDeltaEvent(_RunEventBase):
    type: Literal["assistant_text_delta"] = "assistant_text_delta"
    delta: str


class ApprovalRequestedEvent(_RunEventBase):
    type: Literal["approval_requested"] = "approval_requested"
    request: ApprovalRequest
    tool_name: str | None = None
    tool_call_id: str | None = None


class ApprovalResolvedEvent(_RunEventBase):
    type: Literal["approval_resolved"] = "approval_resolved"
    decision: ApprovalDecision


class OnboardingQuestionRequestedEvent(_RunEventBase):
    type: Literal["onboarding_question_requested"] = "onboarding_question_requested"
    attempt_id: str
    question_type: Literal["mcq"] = "mcq"
    prompt: str
    options: list[str]

    @classmethod
    def from_request(
        cls,
        *,
        run_id: str,
        request: OnboardingQuestionRequest,
    ) -> "OnboardingQuestionRequestedEvent":
        return cls(
            run_id=run_id,
            attempt_id=request.attempt_id,
            question_type=request.question_type,
            prompt=request.prompt,
            options=list(request.options),
        )


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
    transcript_summary: RunTranscriptSummary | None = None


class RunFailedEvent(_RunEventBase):
    type: Literal["run_failed"] = "run_failed"
    error_type: str
    message: str


class InRunCompactionCompletedEvent(_RunEventBase):
    type: Literal["in_run_compaction_completed"] = "in_run_compaction_completed"
    live_message_count: int
    replacement_message_count: int


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


class SessionTurnContextStatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_turn_context_status"] = "session_turn_context_status"
    status: Literal["missing", "reused", "cleared"]
    reason: str
    persisted_run_id: str | None = None


class SessionQueueStateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_queue_state"] = "session_queue_state"
    next_prompts: list[str]
    later_prompts: list[str]


class SessionQueuedPromptBatchSubmittedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["session_queued_prompt_batch_submitted"] = (
        "session_queued_prompt_batch_submitted"
    )
    mode: Literal["next", "later"]
    prompts: list[str]


SessionLifecycleEvent = (
    SessionCompactionStartedEvent
    | SessionCompactionCompletedEvent
    | SessionTurnContextStatusEvent
    | SessionQueueStateEvent
    | SessionQueuedPromptBatchSubmittedEvent
)


RunEvent = Annotated[
    RunStartedEvent
    | AssistantTextDeltaEvent
    | ApprovalRequestedEvent
    | ApprovalResolvedEvent
    | OnboardingQuestionRequestedEvent
    | ToolCallStartedEvent
    | ToolCallUpdatedEvent
    | ToolCallSucceededEvent
    | ToolCallFailedEvent
    | InRunCompactionCompletedEvent
    | RunSucceededEvent
    | RunFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "ActivityGroupCounts",
    "ActivityGroupSummary",
    "ApprovalRequestedEvent",
    "ApprovalResolvedEvent",
    "AssistantTextDeltaEvent",
    "CodeModeActivityDetails",
    "EditActivityDetails",
    "FindActivityDetails",
    "GrepActivityDetails",
    "InRunCompactionCompletedEvent",
    "JsonValue",
    "LsActivityDetails",
    "OnboardingQuestionRequestedEvent",
    "ReadActivityDetails",
    "RunEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "RunSucceededEvent",
    "RunTranscriptSummary",
    "SessionCompactionCompletedEvent",
    "SessionCompactionStartedEvent",
    "SessionLifecycleEvent",
    "SessionQueueStateEvent",
    "SessionQueuedPromptBatchSubmittedEvent",
    "SessionTurnContextStatusEvent",
    "ShellActivityDetails",
    "SubagentActivityDetails",
    "ToolActivity",
    "ToolActivityDetails",
    "ToolCallFailedEvent",
    "ToolCallStartedEvent",
    "ToolCallUpdatedEvent",
    "ToolCallSucceededEvent",
    "WriteActivityDetails",
]
