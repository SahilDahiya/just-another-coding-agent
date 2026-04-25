from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.toolsets import WrapperToolset

from just_another_coding_agent.contracts.sandbox import ApprovalRequestKind
from just_another_coding_agent.contracts.tools import (
    make_tool_denied_result,
    make_tool_error_result,
)


class ToolOperationalError(Exception):
    """Expected tool-domain failure that should be shown to the model."""


class ToolPathError(ToolOperationalError):
    """Path or filesystem failure caused by tool input or target state."""


class ToolEncodingError(ToolOperationalError):
    """File content could not be interpreted as the expected text encoding."""


class ToolMatchError(ToolOperationalError):
    """Requested text match or edit precondition was not satisfied."""


class ToolCommandError(ToolOperationalError):
    """Command execution failed in an expected, model-visible way."""


class ToolApprovalDenied(ToolOperationalError):
    """Approval was denied and the tool must adapt or stop."""

    def __init__(
        self,
        message: str,
        *,
        denial_type: str = "approval_denied",
        approval_kind: ApprovalRequestKind | None = None,
        subject: str | None = None,
        retry_same_request_allowed: bool = False,
    ) -> None:
        super().__init__(message)
        self.denial_type = denial_type
        self.approval_kind = approval_kind
        self.subject = subject
        self.retry_same_request_allowed = retry_same_request_allowed


def reraise_path_error(error: OSError) -> None:
    raise ToolPathError(str(error)) from error


def reraise_encoding_error(
    *,
    path: Path,
    error: UnicodeError,
    message: str | None = None,
) -> None:
    detail = message or f"{path} is not valid UTF-8 text"
    raise ToolEncodingError(detail) from error


@dataclass
class ErrorWrappingToolset(WrapperToolset[Any]):
    async def call_tool(self, name, tool_args, ctx, tool):
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except ToolApprovalDenied as error:
            return make_tool_denied_result(
                message=str(error),
                denial_type=error.denial_type,
                approval_kind=error.approval_kind,
                subject=error.subject,
                retry_same_request_allowed=error.retry_same_request_allowed,
            )
        except ToolOperationalError as error:
            return make_tool_error_result(error)


__all__ = [
    "ErrorWrappingToolset",
    "ToolApprovalDenied",
    "ToolCommandError",
    "ToolEncodingError",
    "ToolMatchError",
    "ToolOperationalError",
    "ToolPathError",
    "reraise_encoding_error",
    "reraise_path_error",
]
