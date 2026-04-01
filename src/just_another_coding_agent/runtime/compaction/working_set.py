from __future__ import annotations

from just_another_coding_agent.contracts.run_events import ToolCallSucceededEvent
from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionSummary,
)
from just_another_coding_agent.runtime.compaction.boundary import (
    runs_since_latest_compaction_boundary,
)
from just_another_coding_agent.tools._activity import shorten_path


def with_deterministic_working_set_paths(
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


__all__ = ["merge_summary_paths", "with_deterministic_working_set_paths"]
