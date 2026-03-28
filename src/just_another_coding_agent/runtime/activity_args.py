from __future__ import annotations

from typing import Any

from just_another_coding_agent.contracts.run_events import (
    BashActivityDetails,
    EditActivityDetails,
    FindActivityDetails,
    GrepActivityDetails,
    LsActivityDetails,
    ReadActivityDetails,
    ToolActivityDetails,
    WriteActivityDetails,
)
from just_another_coding_agent.tools._activity import truncate_activity_label

_TITLE_KEY_BY_TOOL = {
    "read": "path",
    "write": "path",
    "edit": "path",
    "grep": "pattern",
    "find": "pattern",
}


def build_args_tool_title(*, tool_name: str, args: Any, args_valid: bool | None) -> str:
    if args_valid is False or not isinstance(args, dict):
        return tool_name

    if tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return f"bash {truncate_activity_label(command)}"
        return "bash"

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


def build_args_tool_details(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    result: Any,
) -> ToolActivityDetails | None:
    if args_valid is False or not isinstance(args, dict):
        return None

    if tool_name == "bash":
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        exit_code = None
        if isinstance(result, dict):
            raw_exit_code = result.get("exit_code")
            if isinstance(raw_exit_code, int):
                exit_code = raw_exit_code
        return BashActivityDetails(
            command_preview=truncate_activity_label(command),
            timeout=_optional_int(args.get("timeout")),
            deferred=bool(args.get("defer", False)),
            exit_code=exit_code,
        )

    if tool_name == "read":
        path = args.get("path")
        if isinstance(path, str) and path:
            return ReadActivityDetails(
                path=path,
                offset=_optional_int(args.get("offset")),
                limit=_optional_int(args.get("limit")),
            )
        return None

    if tool_name == "write":
        path = args.get("path")
        if isinstance(path, str) and path:
            bytes_written = None
            content = args.get("content")
            if isinstance(content, str) and not (
                isinstance(result, dict) and result.get("ok") is False
            ):
                bytes_written = len(content.encode("utf-8"))
            return WriteActivityDetails(path=path, bytes_written=bytes_written)
        return None

    if tool_name == "edit":
        path = args.get("path")
        if isinstance(path, str) and path:
            return EditActivityDetails(path=path)
        return None

    if tool_name == "grep":
        pattern = args.get("pattern")
        if isinstance(pattern, str) and pattern:
            return GrepActivityDetails(
                pattern=pattern,
                path=_optional_str(args.get("path")),
                glob=_optional_str(args.get("glob")),
                ignore_case=bool(args.get("ignore_case", False)),
                literal=bool(args.get("literal", False)),
                limit=_optional_int(args.get("limit")),
            )
        return None

    if tool_name == "ls":
        return LsActivityDetails(
            path=_optional_str(args.get("path")),
            limit=_optional_int(args.get("limit")),
        )

    if tool_name == "find":
        pattern = args.get("pattern")
        if isinstance(pattern, str) and pattern:
            return FindActivityDetails(
                pattern=pattern,
                path=_optional_str(args.get("path")),
                limit=_optional_int(args.get("limit")),
            )
        return None

    return None


def build_fallback_success_summary(*, tool_name: str, result: Any) -> str | None:
    if isinstance(result, dict) and result.get("ok") is False:
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        return "tool error"

    if tool_name == "bash" and isinstance(result, dict):
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
    }
    return summaries.get(tool_name)


def build_update_summary(*, tool_name: str, partial_result: Any) -> str | None:
    if tool_name == "bash":
        return "command still running"

    if isinstance(partial_result, dict) and partial_result.get("ok") is False:
        message = partial_result.get("message")
        if isinstance(message, str) and message:
            return message

    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


__all__ = [
    "build_args_tool_details",
    "build_args_tool_title",
    "build_fallback_success_summary",
    "build_update_summary",
]
