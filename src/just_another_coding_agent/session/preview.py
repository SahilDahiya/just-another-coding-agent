from __future__ import annotations

from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.contracts.session import (
    SessionPreview,
    SessionPreviewEntry,
)
from just_another_coding_agent.session.jsonl import load_session

SESSION_PREVIEW_MAX_RUNS = 10
SESSION_PREVIEW_MAX_ENTRY_CHARS = 600


def build_session_preview(*, path, workspace_root) -> SessionPreview:
    loaded = load_session(path=path, workspace_root=workspace_root)
    selected_runs = loaded.runs[-SESSION_PREVIEW_MAX_RUNS:]
    entries: list[SessionPreviewEntry] = []
    truncated = len(loaded.runs) > len(selected_runs)

    for run in selected_runs:
        prompt_text, prompt_truncated = _truncate_preview_text(run.prompt)
        if prompt_text:
            entries.append(SessionPreviewEntry(kind="user", text=prompt_text))
            truncated = truncated or prompt_truncated

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
    return normalized[: max_chars - 1].rstrip() + "…", True


__all__ = [
    "SESSION_PREVIEW_MAX_ENTRY_CHARS",
    "SESSION_PREVIEW_MAX_RUNS",
    "build_session_preview",
]
