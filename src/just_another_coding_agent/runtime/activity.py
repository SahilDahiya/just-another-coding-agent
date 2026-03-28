from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from just_another_coding_agent.contracts.run_events import (
    ToolActivity,
    ToolActivityDetails,
)
from just_another_coding_agent.runtime.activity_args import (
    build_args_tool_details,
    build_args_tool_title,
    build_fallback_success_summary,
    build_update_summary,
)

_TOOL_ACTIVITY_DETAILS_ADAPTER = TypeAdapter(ToolActivityDetails)


def build_started_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
) -> ToolActivity:
    return ToolActivity(
        title=build_args_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        details=build_args_tool_details(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
            result=None,
        ),
    )


def build_succeeded_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    result: Any,
    result_metadata: Any = None,
    duration_ms: int,
) -> ToolActivity:
    if result_metadata is not None:
        return _build_tool_activity_from_metadata(
            result_metadata=result_metadata,
            duration_ms=duration_ms,
        )

    return ToolActivity(
        title=build_args_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        summary=build_fallback_success_summary(tool_name=tool_name, result=result),
        duration_ms=duration_ms,
        details=build_args_tool_details(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
            result=result,
        ),
    )


def build_updated_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    partial_result: Any,
    duration_ms: int,
) -> ToolActivity:
    return ToolActivity(
        title=build_args_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        summary=build_update_summary(
            tool_name=tool_name,
            partial_result=partial_result,
        ),
        duration_ms=duration_ms,
        details=build_args_tool_details(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
            result=partial_result,
        ),
    )


def build_failed_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    message: str,
    duration_ms: int,
) -> ToolActivity:
    return ToolActivity(
        title=build_args_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        summary=message,
        duration_ms=duration_ms,
        details=build_args_tool_details(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
            result=None,
        ),
    )


def _build_tool_activity_from_metadata(
    *,
    result_metadata: Any,
    duration_ms: int,
) -> ToolActivity:
    if not isinstance(result_metadata, dict):
        raise TypeError("Tool activity metadata must be a dict")

    title = result_metadata.get("title")
    if not isinstance(title, str) or not title:
        raise TypeError("Tool activity metadata title must be a non-empty string")

    summary = result_metadata.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise TypeError("Tool activity metadata summary must be a string or None")

    details = result_metadata.get("details")
    validated_details = None
    if details is not None:
        validated_details = _TOOL_ACTIVITY_DETAILS_ADAPTER.validate_python(details)

    return ToolActivity(
        title=title,
        summary=summary,
        duration_ms=duration_ms,
        details=validated_details,
    )


__all__ = [
    "build_failed_tool_activity",
    "build_started_tool_activity",
    "build_succeeded_tool_activity",
    "build_updated_tool_activity",
]
