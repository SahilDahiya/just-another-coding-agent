from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pydantic import TypeAdapter

from just_another_coding_agent.contracts.run_events import (
    JsonValue,
    ToolActivity,
    ToolActivityDetails,
    ToolCallFailedEvent,
)
from just_another_coding_agent.tools._activity import truncate_activity_label

_TOOL_ACTIVITY_DETAILS_ADAPTER = TypeAdapter(ToolActivityDetails)
_TITLE_KEY_BY_TOOL = {
    "ask_mcq_question": "question",
    "generate_mcq_from_teaching_packets": "packet_ids",
    "publish_teaching_packet": "title",
    "read": "path",
    "write": "path",
    "edit": "path",
    "grep": "pattern",
    "find": "pattern",
}
_DISPLAY_LABEL_BY_TOOL = {
    "ask_mcq_question": "Onboard",
    "generate_mcq_from_teaching_packets": "Onboard",
    "publish_teaching_packet": "Teach",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "shell": "Shell",
    "grep": "Search",
    "ls": "List",
    "find": "Find",
    "subagent": "Subagent",
}
_EXPLORATION_TOOL_NAMES = frozenset({"read", "grep", "ls", "find"})
_SUBAGENT_DISPLAY_LABEL_BY_ROLE = {
    "general": "Subagent",
    "explore": "Explore",
    "verification": "Verify",
}


@dataclass(frozen=True)
class PendingToolCall:
    tool_call_id: str
    tool_name: str
    args: JsonValue | None
    args_valid: bool | None
    started_at: float


def _group_kind_for_tool(tool_name: str) -> str | None:
    if tool_name in _EXPLORATION_TOOL_NAMES:
        return "exploration"
    return None


def _display_label_for_tool(tool_name: str) -> str | None:
    return _DISPLAY_LABEL_BY_TOOL.get(tool_name)


def _display_label_for_subagent(args: Any, args_valid: bool | None) -> str | None:
    if args_valid is False or not isinstance(args, dict):
        return _DISPLAY_LABEL_BY_TOOL["subagent"]
    role = args.get("role")
    if isinstance(role, str):
        return _SUBAGENT_DISPLAY_LABEL_BY_ROLE.get(
            role,
            _DISPLAY_LABEL_BY_TOOL["subagent"],
        )
    return _DISPLAY_LABEL_BY_TOOL["subagent"]


def build_started_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
) -> ToolActivity:
    group_kind = _group_kind_for_tool(tool_name)
    display_label = (
        _display_label_for_subagent(args, args_valid)
        if tool_name == "subagent"
        else _display_label_for_tool(tool_name)
    )
    return ToolActivity(
        title=_build_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        display_label=display_label,
        group_kind=group_kind,
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
    group_kind = _group_kind_for_tool(tool_name)
    display_label = (
        _display_label_for_subagent(args, args_valid)
        if tool_name == "subagent"
        else _display_label_for_tool(tool_name)
    )

    if result_metadata is not None:
        activity = _build_tool_activity_from_metadata(
            result_metadata=result_metadata,
            duration_ms=duration_ms,
        )
        updates: dict[str, Any] = {}
        if activity.display_label is None:
            updates["display_label"] = display_label
        if activity.group_kind is None:
            updates["group_kind"] = group_kind
        if updates:
            activity = activity.model_copy(update=updates)
        return activity

    return ToolActivity(
        title=_build_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        display_label=display_label,
        summary=_build_fallback_success_summary(tool_name=tool_name, result=result),
        duration_ms=duration_ms,
        group_kind=group_kind,
    )


def build_updated_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    partial_result: Any,
    duration_ms: int,
) -> ToolActivity:
    group_kind = _group_kind_for_tool(tool_name)
    display_label = (
        _display_label_for_subagent(args, args_valid)
        if tool_name == "subagent"
        else _display_label_for_tool(tool_name)
    )
    summary = None
    details = None
    if tool_name == "shell":
        summary = "command still running"
    elif tool_name == "subagent" and isinstance(partial_result, dict):
        partial_summary = partial_result.get("summary")
        if isinstance(partial_summary, str) and partial_summary.strip():
            summary = partial_summary
        partial_details = partial_result.get("details")
        if partial_details is not None:
            details = _TOOL_ACTIVITY_DETAILS_ADAPTER.validate_python(partial_details)
    return ToolActivity(
        title=_build_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        display_label=display_label,
        summary=summary,
        duration_ms=duration_ms,
        details=details,
        group_kind=group_kind,
    )


def build_failed_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    message: str,
    duration_ms: int,
) -> ToolActivity:
    group_kind = _group_kind_for_tool(tool_name)
    display_label = (
        _display_label_for_subagent(args, args_valid)
        if tool_name == "subagent"
        else _display_label_for_tool(tool_name)
    )
    return ToolActivity(
        title=_build_tool_title(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
        ),
        display_label=display_label,
        summary=message,
        duration_ms=duration_ms,
        group_kind=group_kind,
    )


def synthesize_tool_failed_events_for_pending(
    *,
    run_id: str,
    pending: Iterable[PendingToolCall],
    error_type: str,
    message: str,
) -> list[ToolCallFailedEvent]:
    return [
        ToolCallFailedEvent(
            run_id=run_id,
            tool_call_id=pending_tool_call.tool_call_id,
            tool_name=pending_tool_call.tool_name,
            error_type=error_type,
            message=message,
            activity=build_failed_tool_activity(
                tool_name=pending_tool_call.tool_name,
                args=pending_tool_call.args,
                args_valid=pending_tool_call.args_valid,
                message=message,
                duration_ms=_duration_ms_since(pending_tool_call.started_at),
            ),
        )
        for pending_tool_call in pending
    ]


def _build_tool_title(*, tool_name: str, args: Any, args_valid: bool | None) -> str:
    if args_valid is False or not isinstance(args, dict):
        return tool_name

    if tool_name == "subagent":
        name = args.get("name")
        if isinstance(name, str) and name.strip():
            return f"subagent {truncate_activity_label(name)}"
        task = args.get("task")
        if isinstance(task, str) and task.strip():
            return f"subagent {truncate_activity_label(task)}"
        return "subagent"

    if tool_name == "shell":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return f"shell {truncate_activity_label(command)}"
        return "shell"

    if tool_name == "ls":
        path = args.get("path")
        if isinstance(path, str) and path.strip():
            return f"ls {truncate_activity_label(path)}"
        return "ls ."

    key = _TITLE_KEY_BY_TOOL.get(tool_name)
    value = args.get(key) if key is not None else None
    if isinstance(value, str) and value.strip():
        return f"{tool_name} {truncate_activity_label(value)}"
    return tool_name


def _build_fallback_success_summary(*, tool_name: str, result: Any) -> str | None:
    if isinstance(result, dict) and result.get("outcome") == "denied":
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        return "tool denied"

    if isinstance(result, dict) and result.get("ok") is False:
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        return "tool error"

    if tool_name == "shell" and isinstance(result, dict):
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return f"command exited {exit_code}"

    summaries = {
        "read": "read completed",
        "write": "wrote file",
        "edit": "edit applied",
        "grep": "search completed",
        "ls": "listing completed",
        "find": "find completed",
        "subagent": "subagent completed",
        "generate_mcq_from_teaching_packets": "generated MCQ draft",
    }
    return summaries.get(tool_name)


def _duration_ms_since(started_at: float) -> int:
    if started_at <= 0:
        return 0
    return max(0, int((monotonic() - started_at) * 1000))


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

    display_label = result_metadata.get("display_label")
    if display_label is not None and not isinstance(display_label, str):
        raise TypeError("Tool activity metadata display_label must be a string or None")

    details = result_metadata.get("details")
    validated_details = None
    if details is not None:
        validated_details = _TOOL_ACTIVITY_DETAILS_ADAPTER.validate_python(details)

    return ToolActivity(
        title=title,
        display_label=display_label,
        summary=summary,
        duration_ms=duration_ms,
        details=validated_details,
    )


__all__ = [
    "PendingToolCall",
    "build_failed_tool_activity",
    "build_started_tool_activity",
    "build_succeeded_tool_activity",
    "build_updated_tool_activity",
    "synthesize_tool_failed_events_for_pending",
]
