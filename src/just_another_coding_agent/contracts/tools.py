from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from just_another_coding_agent.contracts.sandbox import ApprovalRequestKind

CANONICAL_TOOL_NAMES = (
    "read",
    "write",
    "edit",
    "shell",
    "grep",
    "ls",
    "find",
    "subagent",
)
ONBOARDING_TOOL_NAMES = (
    "ask_mcq_question",
    "publish_teaching_packet",
)
KNOWN_TOOL_NAMES = (*CANONICAL_TOOL_NAMES, *ONBOARDING_TOOL_NAMES)

CanonicalToolName = Literal[
    "read",
    "write",
    "edit",
    "shell",
    "grep",
    "ls",
    "find",
    "subagent",
]
OnboardingToolName = Literal[
    "ask_mcq_question",
    "publish_teaching_packet",
]
KnownToolName = Literal[
    "read",
    "write",
    "edit",
    "shell",
    "grep",
    "ls",
    "find",
    "subagent",
    "ask_mcq_question",
    "publish_teaching_packet",
]


class ToolErrorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ok: Literal[False] = False
    error_type: str
    message: str


class ToolDeniedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ok: Literal[False] = False
    outcome: Literal["denied"] = "denied"
    denial_type: str
    message: str
    approval_kind: ApprovalRequestKind | None = None
    subject: str | None = None
    retry_same_request_allowed: bool | None = None


def make_tool_error_result(error: Exception) -> dict[str, bool | str]:
    return ToolErrorResult(
        error_type=type(error).__name__,
        message=str(error),
    ).model_dump(mode="json")


def make_tool_denied_result(
    *,
    message: str,
    denial_type: str = "approval_denied",
    approval_kind: ApprovalRequestKind | None = None,
    subject: str | None = None,
    retry_same_request_allowed: bool | None = None,
) -> dict[str, bool | str]:
    return ToolDeniedResult(
        denial_type=denial_type,
        message=message,
        approval_kind=approval_kind,
        subject=subject,
        retry_same_request_allowed=retry_same_request_allowed,
    ).model_dump(mode="json")


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "CanonicalToolName",
    "ONBOARDING_TOOL_NAMES",
    "OnboardingToolName",
    "ToolDeniedResult",
    "ToolErrorResult",
    "make_tool_denied_result",
    "make_tool_error_result",
]
