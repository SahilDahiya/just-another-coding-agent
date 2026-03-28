from __future__ import annotations

from typing import Any

from just_another_coding_agent.contracts.run_events import (
    BashActivityDetails,
    EditActivityDetails,
    FindActivityDetails,
    GrepActivityDetails,
    LsActivityDetails,
    ReadActivityDetails,
    ToolActivity,
    ToolActivityDetails,
    WriteActivityDetails,
)


def build_started_tool_activity(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
) -> ToolActivity:
    return ToolActivity(
        title=_build_tool_title(tool_name=tool_name, args=args, args_valid=args_valid),
        details=_build_tool_details(
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
    duration_ms: int,
) -> ToolActivity:
    return ToolActivity(
        title=_build_tool_title(tool_name=tool_name, args=args, args_valid=args_valid),
        summary=_build_success_summary(tool_name=tool_name, result=result),
        duration_ms=duration_ms,
        details=_build_tool_details(
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
        title=_build_tool_title(tool_name=tool_name, args=args, args_valid=args_valid),
        summary=_build_update_summary(
            tool_name=tool_name,
            partial_result=partial_result,
        ),
        duration_ms=duration_ms,
        details=_build_tool_details(
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
        title=_build_tool_title(tool_name=tool_name, args=args, args_valid=args_valid),
        summary=message,
        duration_ms=duration_ms,
        details=_build_tool_details(
            tool_name=tool_name,
            args=args,
            args_valid=args_valid,
            result=None,
        ),
    )


def _build_tool_title(*, tool_name: str, args: Any, args_valid: bool | None) -> str:
    if args_valid is False or not isinstance(args, dict):
        return tool_name

    if tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return f"bash {_truncate_inline(command)}"
        return "bash"

    key_by_tool = {
        "read": "path",
        "write": "path",
        "edit": "path",
        "grep": "pattern",
        "ls": "path",
        "find": "pattern",
    }
    key = key_by_tool.get(tool_name)
    value = args.get(key) if key is not None else None
    if isinstance(value, str) and value.strip():
        return f"{tool_name} {_truncate_inline(value)}"
    if tool_name == "ls":
        return "ls ."
    return tool_name


def _build_success_summary(*, tool_name: str, result: Any) -> str | None:
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


def _build_update_summary(*, tool_name: str, partial_result: Any) -> str | None:
    if tool_name == "bash":
        return "command still running"

    if isinstance(partial_result, dict) and partial_result.get("ok") is False:
        message = partial_result.get("message")
        if isinstance(message, str) and message:
            return message

    return None


def _build_tool_details(
    *,
    tool_name: str,
    args: Any,
    args_valid: bool | None,
    result: Any,
) -> ToolActivityDetails | None:
    # Keep this dispatch in sync with CANONICAL_TOOL_NAMES. The representative
    # coverage test in tests/contracts/test_activity_metadata.py is intended to
    # fail when a new canonical tool lands without an explicit details branch.
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
            command_preview=_truncate_inline(command),
            timeout=_optional_int(args.get("timeout")),
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


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _truncate_inline(text: str, *, limit: int = 56) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


__all__ = [
    "build_failed_tool_activity",
    "build_started_tool_activity",
    "build_succeeded_tool_activity",
    "build_updated_tool_activity",
]
