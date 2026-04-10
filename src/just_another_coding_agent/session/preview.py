from __future__ import annotations

from just_another_coding_agent.contracts.run_events import (
    ActivityGroupSummary,
    RunFailedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.contracts.session import (
    SessionPreview,
    SessionPreviewEntry,
)
from just_another_coding_agent.runtime.project_docs import (
    build_project_doc_notice_line,
)
from just_another_coding_agent.session.jsonl import load_session

SESSION_PREVIEW_MAX_RUNS = 10
SESSION_PREVIEW_MAX_ENTRY_CHARS = 600


def build_session_preview(*, path, workspace_root) -> SessionPreview:
    loaded = load_session(path=path, workspace_root=workspace_root)
    selected_runs = loaded.runs[-SESSION_PREVIEW_MAX_RUNS:]
    entries: list[SessionPreviewEntry] = []
    truncated = len(loaded.runs) > len(selected_runs)

    if loaded.project_docs is not None:
        notice_line = build_project_doc_notice_line(
            tuple(
                (doc.short_path, doc.truncated)
                for doc in loaded.project_docs.documents
            )
        )
        if notice_line:
            entries.append(
                SessionPreviewEntry(kind="instructions", text=notice_line)
            )

    for run in selected_runs:
        prompt_text, prompt_truncated = _truncate_preview_text(run.prompt)
        if prompt_text:
            entries.append(SessionPreviewEntry(kind="user", text=prompt_text))
            truncated = truncated or prompt_truncated

        for activity_text in _activity_preview_for_run(run):
            preview_text, text_truncated = _truncate_preview_text(activity_text)
            if preview_text:
                entries.append(
                    SessionPreviewEntry(kind="activity", text=preview_text)
                )
                truncated = truncated or text_truncated

        terminal_text, terminal_kind = _terminal_preview_for_run(run)
        if terminal_text is None:
            continue
        preview_text, text_truncated = _truncate_preview_text(terminal_text)
        if preview_text:
            entries.append(
                SessionPreviewEntry(kind=terminal_kind, text=preview_text)
            )
            truncated = truncated or text_truncated

    return SessionPreview(
        session_id=path.stem,
        entries=entries,
        truncated=truncated,
    )


def _terminal_preview_for_run(run) -> tuple[str | None, str]:
    for event in reversed(run.events):
        if isinstance(event, RunSucceededEvent):
            return event.output_text, "assistant"
        if isinstance(event, RunFailedEvent):
            return event.message, "error"
    return None, "assistant"


def _activity_preview_for_run(run) -> list[str]:
    for event in reversed(run.events):
        if isinstance(event, RunSucceededEvent):
            summary = event.transcript_summary
            if summary is None:
                return []
            return [
                text
                for group in summary.activity_groups
                if (text := _format_activity_group_preview(group))
            ]
        if isinstance(event, RunFailedEvent):
            return []
    return []


def _format_activity_group_preview(group: ActivityGroupSummary) -> str | None:
    if _is_generic_shell_group(group):
        return None
    parts = [group.group_label]
    count = _format_group_count(group)
    if count:
        parts.append(count)
    if group.elapsed_ms is not None:
        parts.append(_format_duration_ms(group.elapsed_ms))
    if group.outcome != "success":
        parts.append(group.outcome.replace("_", " "))
    return " - ".join(parts)


def _is_generic_shell_group(group: ActivityGroupSummary) -> bool:
    counts = group.group_counts
    known_non_shell_count = (
        counts.read
        + counts.search
        + counts.list
        + counts.write
        + counts.edit
    )
    unknown_tool_count = max(counts.tool - counts.shell - known_non_shell_count, 0)
    return (
        group.group_kind == "execution"
        and group.group_label == "Shell"
        and counts.shell > 0
        and known_non_shell_count == 0
        and unknown_tool_count == 0
    )


def _format_group_count(group: ActivityGroupSummary) -> str | None:
    counts = group.group_counts
    if counts.shell:
        return _plural(counts.shell, "command")
    changed_files = counts.write + counts.edit
    if changed_files:
        return _plural(changed_files, "file")
    exploration_ops = counts.read + counts.search + counts.list
    if exploration_ops:
        return _plural(exploration_ops, "operation")
    if counts.tool:
        return _plural(counts.tool, "tool")
    return None


def _plural(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


def _format_duration_ms(duration_ms: int) -> str:
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1000
    if seconds < 60:
        formatted = f"{seconds:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}s"
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    return f"{minutes}m {remaining_seconds:02d}s"


def _truncate_preview_text(
    text: str,
    *,
    max_chars: int = SESSION_PREVIEW_MAX_ENTRY_CHARS,
) -> tuple[str, bool]:
    normalized = text.strip()
    if not normalized:
        return "", False
    if len(normalized) <= max_chars:
        return normalized, False
    if max_chars <= 3:
        return normalized[:max_chars], True
    return normalized[: max_chars - 3].rstrip() + "...", True


__all__ = [
    "SESSION_PREVIEW_MAX_ENTRY_CHARS",
    "SESSION_PREVIEW_MAX_RUNS",
    "build_session_preview",
]
