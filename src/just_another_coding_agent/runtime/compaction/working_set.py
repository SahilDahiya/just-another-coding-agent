from __future__ import annotations

from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    ShellActivityDetails,
    ToolCallFailedEvent,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction_boundary,
)
from just_another_coding_agent.runtime.compaction.constants import (
    MAX_COMPACTION_RECENT_FAILURES,
    MAX_COMPACTION_RECENT_SHELL_COMMANDS,
)
from just_another_coding_agent.runtime.compaction.source_builder import compact_text
from just_another_coding_agent.tools._activity import shorten_path


def with_deterministic_survival_state(
    summary: SessionCompactionSummary,
    *,
    loaded_session: LoadedSession,
) -> SessionCompactionSummary:
    latest_compaction = loaded_session.latest_compaction
    previous_read_paths = (
        latest_compaction.summary.read_paths if latest_compaction is not None else []
    )
    previous_modified_paths = (
        latest_compaction.summary.modified_paths
        if latest_compaction is not None
        else []
    )
    previous_recent_shell_commands = (
        latest_compaction.summary.recent_shell_commands
        if latest_compaction is not None
        else []
    )
    previous_recent_failures = (
        latest_compaction.summary.recent_failures
        if latest_compaction is not None
        else []
    )

    return SessionCompactionSummary(
        current_objective=summary.current_objective,
        established_facts=summary.established_facts,
        user_preferences=summary.user_preferences,
        important_paths=summary.important_paths,
        read_paths=merge_summary_paths(
            previous_read_paths,
            _collect_recent_paths_for_tools(
                loaded_session,
                tool_names={"read"},
            ),
        ),
        modified_paths=merge_summary_paths(
            previous_modified_paths,
            _collect_recent_paths_for_tools(
                loaded_session,
                tool_names={"write", "edit"},
            ),
        ),
        recent_shell_commands=_merge_recent_summary_items(
            previous_recent_shell_commands,
            _collect_recent_shell_commands(loaded_session),
            limit=MAX_COMPACTION_RECENT_SHELL_COMMANDS,
        ),
        recent_failures=_merge_recent_summary_items(
            previous_recent_failures,
            _collect_recent_failures(loaded_session),
            limit=MAX_COMPACTION_RECENT_FAILURES,
        ),
        open_questions=summary.open_questions,
        unresolved_work=summary.unresolved_work,
    )


def _collect_recent_paths_for_tools(
    loaded_session: LoadedSession,
    *,
    tool_names: set[str],
) -> list[str]:
    collected: list[str] = []
    for run in runs_since_latest_compaction_boundary(loaded_session):
        for event in run.events:
            if not isinstance(event, ToolCallSucceededEvent):
                continue
            if event.tool_name not in tool_names:
                continue
            path = _extract_activity_path(
                event,
                workspace_root=loaded_session.header.workspace_root,
            )
            if path is not None:
                collected.append(path)
    return merge_summary_paths(collected)


def _extract_activity_path(
    event: ToolCallSucceededEvent,
    *,
    workspace_root: str,
) -> str | None:
    activity = event.activity
    details = activity.details if activity is not None else None
    if details is None:
        return None

    short_path = getattr(details, "short_path", None)
    if isinstance(short_path, str) and short_path.strip():
        return short_path.strip()

    path = getattr(details, "path", None)
    if not isinstance(path, str) or not path.strip():
        return None
    return shorten_path(path.strip(), workspace_root)


def _collect_recent_shell_commands(loaded_session: LoadedSession) -> list[str]:
    commands: list[str] = []
    for run in runs_since_latest_compaction_boundary(loaded_session):
        for event in run.events:
            if isinstance(event, ToolCallSucceededEvent) and event.tool_name == "shell":
                command = _format_recent_shell_command(event)
                if command is not None:
                    commands.append(command)
                continue
            if isinstance(event, ToolCallFailedEvent) and event.tool_name == "shell":
                command = _format_failed_shell_command(event)
                if command is not None:
                    commands.append(command)
    return _merge_recent_summary_items(
        commands,
        limit=MAX_COMPACTION_RECENT_SHELL_COMMANDS,
    )


def _format_recent_shell_command(event: ToolCallSucceededEvent) -> str | None:
    activity = event.activity
    if activity is None:
        return None

    command_preview = _shell_command_preview(
        title=activity.title,
        details=activity.details,
    )
    if command_preview is None:
        return None

    details = activity.details
    if isinstance(details, ShellActivityDetails) and details.exit_code is not None:
        return compact_text(f"{command_preview} (exit {details.exit_code})")
    if activity.summary:
        return compact_text(f"{command_preview} ({activity.summary})")
    return compact_text(command_preview)


def _format_failed_shell_command(event: ToolCallFailedEvent) -> str | None:
    activity = event.activity
    title = activity.title if activity is not None else event.tool_name
    command_preview = _shell_command_preview(
        title=title,
        details=activity.details if activity is not None else None,
    )
    if command_preview is None:
        return None
    return compact_text(f"{command_preview} (failed)")


def _shell_command_preview(title: str, details: object | None) -> str | None:
    if isinstance(details, ShellActivityDetails):
        command_preview = details.command_preview.strip()
        if command_preview:
            return command_preview

    normalized_title = title.strip()
    if not normalized_title:
        return None
    if normalized_title.startswith("shell "):
        normalized_title = normalized_title[6:]
    return normalized_title or None


def _collect_recent_failures(loaded_session: LoadedSession) -> list[str]:
    failures: list[str] = []
    for run in runs_since_latest_compaction_boundary(loaded_session):
        for event in run.events:
            if isinstance(event, ToolCallFailedEvent):
                failures.append(_format_recent_tool_failure(event))
                continue
            if isinstance(event, RunFailedEvent):
                failures.append(
                    compact_text(f"run failed ({event.error_type}): {event.message}")
                )
    return _merge_recent_summary_items(failures, limit=MAX_COMPACTION_RECENT_FAILURES)


def _format_recent_tool_failure(event: ToolCallFailedEvent) -> str:
    activity = event.activity
    title = activity.title if activity is not None else event.tool_name
    return compact_text(f"{title} failed: {event.message}")


def merge_summary_paths(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _merge_recent_summary_items(*groups: list[str], limit: int) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for value in group:
            item = value.strip()
            if not item:
                continue
            if item in merged:
                merged.remove(item)
            merged.append(item)
    if len(merged) > limit:
        return merged[-limit:]
    return merged


__all__ = ["merge_summary_paths", "with_deterministic_survival_state"]
