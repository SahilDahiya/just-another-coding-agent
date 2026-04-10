from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass

from just_another_coding_agent.contracts.run_events import (
    ActivityGroupCounts,
    ActivityGroupSummary,
    AssistantTextDeltaEvent,
    EditActivityDetails,
    FindActivityDetails,
    GrepActivityDetails,
    LsActivityDetails,
    ReadActivityDetails,
    RunEvent,
    RunSucceededEvent,
    RunTranscriptSummary,
    ShellActivityDetails,
    ToolActivity,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
    WriteActivityDetails,
)

_SEPARATOR_MIN_ELAPSED_MS = 60_000
_SEPARATOR_MIN_TOTAL_TOKENS = 100_000
_SEPARATOR_MIN_CONTEXT_USED = 0.40
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "branch",
        "diff",
        "log",
        "ls-files",
        "rev-parse",
        "show",
        "status",
    }
)
_TEST_COMMANDS = frozenset({"cargo", "go", "jest", "pytest", "tox"})
_PACKAGE_MANAGER_TEST_COMMANDS = frozenset({"npm", "pnpm", "yarn"})


@dataclass
class _GroupAccumulator:
    group_kind: str
    group_label: str
    group_counts: ActivityGroupCounts
    display_hint: str | None
    outcome: str
    elapsed_ms: int

    def can_absorb(self, *, group_kind: str, group_label: str, outcome: str) -> bool:
        return (
            self.group_kind == group_kind
            and self.group_label == group_label
            and self.outcome == outcome
        )

    def add(
        self,
        *,
        group_counts: ActivityGroupCounts,
        display_hint: str | None,
        elapsed_ms: int,
    ) -> None:
        self.group_counts = ActivityGroupCounts(
            read=self.group_counts.read + group_counts.read,
            search=self.group_counts.search + group_counts.search,
            list=self.group_counts.list + group_counts.list,
            shell=self.group_counts.shell + group_counts.shell,
            write=self.group_counts.write + group_counts.write,
            edit=self.group_counts.edit + group_counts.edit,
            tool=self.group_counts.tool + group_counts.tool,
        )
        if display_hint:
            self.display_hint = display_hint
        self.elapsed_ms += elapsed_ms

    def finish(self) -> ActivityGroupSummary:
        return ActivityGroupSummary(
            group_kind=self.group_kind,
            group_label=self.group_label,
            group_counts=self.group_counts,
            display_hint=self.display_hint,
            outcome=self.outcome,
            elapsed_ms=self.elapsed_ms or None,
        )


def build_run_transcript_summary(
    *,
    events: Sequence[RunEvent],
    terminal_event: RunSucceededEvent,
    elapsed_ms: int,
) -> RunTranscriptSummary:
    activity_groups = _build_activity_groups(events)
    tool_call_count = sum(group.group_counts.tool for group in activity_groups)
    tool_duration_ms = sum(group.elapsed_ms or 0 for group in activity_groups)
    had_work_activity = tool_call_count > 0
    summary = RunTranscriptSummary(
        elapsed_ms=elapsed_ms,
        tool_call_count=tool_call_count,
        tool_duration_ms=tool_duration_ms,
        input_tokens=terminal_event.input_tokens,
        output_tokens=terminal_event.output_tokens,
        total_tokens=terminal_event.total_tokens,
        context_window_used=terminal_event.context_window_used,
        next_request_context_window_used=terminal_event.next_request_context_window_used,
        had_work_activity=had_work_activity,
        activity_groups=activity_groups,
    )
    return summary.model_copy(
        update={"should_show_separator": _should_show_separator(summary)}
    )


def sync_run_transcript_summary_metrics(
    event: RunSucceededEvent,
) -> RunSucceededEvent:
    if event.transcript_summary is None:
        return event

    summary = event.transcript_summary.model_copy(
        update={
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "total_tokens": event.total_tokens,
            "context_window_used": event.context_window_used,
            "next_request_context_window_used": event.next_request_context_window_used,
        }
    )
    summary = summary.model_copy(
        update={"should_show_separator": _should_show_separator(summary)}
    )
    return event.model_copy(update={"transcript_summary": summary})


def _build_activity_groups(events: Sequence[RunEvent]) -> list[ActivityGroupSummary]:
    groups: list[ActivityGroupSummary] = []
    current: _GroupAccumulator | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            groups.append(current.finish())
            current = None

    for event in events:
        if isinstance(event, AssistantTextDeltaEvent) and event.delta.strip():
            flush()
            continue
        if not isinstance(event, ToolCallSucceededEvent | ToolCallFailedEvent):
            continue

        activity = event.activity
        if activity is None:
            flush()
            continue

        outcome = _event_outcome(event)
        group_kind, group_label = _group_identity(
            activity=activity,
            tool_name=event.tool_name,
        )
        group_counts = _group_counts(activity=activity, tool_name=event.tool_name)
        display_hint = _display_hint(activity)
        elapsed_ms = activity.duration_ms or 0

        if (
            current is not None
            and current.can_absorb(
                group_kind=group_kind,
                group_label=group_label,
                outcome=outcome,
            )
        ):
            current.add(
                group_counts=group_counts,
                display_hint=display_hint,
                elapsed_ms=elapsed_ms,
            )
            continue

        flush()
        current = _GroupAccumulator(
            group_kind=group_kind,
            group_label=group_label,
            group_counts=group_counts,
            display_hint=display_hint,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
        )

    flush()
    return groups


def _group_identity(*, activity: ToolActivity, tool_name: str) -> tuple[str, str]:
    details = activity.details
    if isinstance(details, ShellActivityDetails):
        return "execution", _shell_group_label(details.command_preview)
    if isinstance(details, WriteActivityDetails | EditActivityDetails):
        return "editing", "Edited files"
    if activity.group_kind == "exploration":
        return "exploration", "Read/Searched"
    if activity.group_kind in {"execution", "editing", "compaction", "other"}:
        return activity.group_kind, activity.display_label or activity.title
    return "other", activity.display_label or tool_name


def _group_counts(*, activity: ToolActivity, tool_name: str) -> ActivityGroupCounts:
    details = activity.details
    if isinstance(details, ShellActivityDetails) or tool_name == "shell":
        return ActivityGroupCounts(shell=1, tool=1)
    if isinstance(details, ReadActivityDetails) or tool_name == "read":
        return ActivityGroupCounts(read=1, tool=1)
    if isinstance(details, GrepActivityDetails) or tool_name == "grep":
        return ActivityGroupCounts(search=1, tool=1)
    if isinstance(details, LsActivityDetails) or tool_name == "ls":
        return ActivityGroupCounts(list=1, tool=1)
    if isinstance(details, FindActivityDetails) or tool_name == "find":
        return ActivityGroupCounts(search=1, tool=1)
    if isinstance(details, WriteActivityDetails) or tool_name == "write":
        return ActivityGroupCounts(write=1, tool=1)
    if isinstance(details, EditActivityDetails) or tool_name == "edit":
        return ActivityGroupCounts(edit=1, tool=1)
    return ActivityGroupCounts(tool=1)


def _display_hint(activity: ToolActivity) -> str | None:
    details = activity.details
    if isinstance(details, ShellActivityDetails):
        return details.command_preview
    if isinstance(details, ReadActivityDetails):
        return details.short_path or details.path
    if isinstance(details, GrepActivityDetails):
        return details.pattern
    if isinstance(details, LsActivityDetails):
        return details.short_path or details.path or "."
    if isinstance(details, FindActivityDetails):
        return details.pattern
    if isinstance(details, WriteActivityDetails | EditActivityDetails):
        return details.path
    return activity.summary or activity.title


def _event_outcome(event: ToolCallSucceededEvent | ToolCallFailedEvent) -> str:
    if isinstance(event, ToolCallFailedEvent):
        return "error"
    if isinstance(event.result, dict) and event.result.get("ok") is False:
        return "operational_miss"
    return "success"


def _shell_group_label(command: str) -> str:
    argv = _split_command(command)
    if _is_git_inspection_command(argv):
        return "Git check"
    if _is_test_command(argv):
        return "Ran tests"
    return "Shell"


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _is_git_inspection_command(argv: Sequence[str]) -> bool:
    if len(argv) < 2 or argv[0] != "git":
        return False
    for arg in argv[1:]:
        if arg in {"-C", "-c"}:
            return False
        if arg.startswith("-"):
            continue
        return arg in _READ_ONLY_GIT_SUBCOMMANDS
    return False


def _is_test_command(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    if argv[0] in _TEST_COMMANDS:
        return argv[0] != "go" or (len(argv) > 1 and argv[1] == "test")
    if argv[0] == "uv":
        return len(argv) > 2 and argv[1] == "run" and _is_test_command(argv[2:])
    if argv[0] in _PACKAGE_MANAGER_TEST_COMMANDS:
        if len(argv) > 1 and argv[1] == "test":
            return True
        return len(argv) > 2 and argv[1] == "run" and argv[2].startswith("test")
    return False


def _should_show_separator(summary: RunTranscriptSummary) -> bool:
    if not summary.had_work_activity:
        return False
    if summary.elapsed_ms >= _SEPARATOR_MIN_ELAPSED_MS:
        return True
    if (
        summary.total_tokens is not None
        and summary.total_tokens >= _SEPARATOR_MIN_TOTAL_TOKENS
    ):
        return True
    context_values = (
        summary.context_window_used,
        summary.next_request_context_window_used,
    )
    return any(
        value is not None and value >= _SEPARATOR_MIN_CONTEXT_USED
        for value in context_values
    )


__all__ = [
    "build_run_transcript_summary",
    "sync_run_transcript_summary_metrics",
]
