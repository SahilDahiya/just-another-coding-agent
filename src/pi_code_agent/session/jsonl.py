from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import ModelMessage

from pi_code_agent.contracts.run_events import (
    RunEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
)
from pi_code_agent.contracts.session import (
    SESSION_FORMAT_VERSION,
    LoadedSession,
    SessionEntry,
    SessionEventEntry,
    SessionHeaderEntry,
    SessionMessagesEntry,
    SessionRunEntry,
    SessionRunRecord,
)
from pi_code_agent.tools._workspace import normalize_workspace_root

_SESSION_ENTRY_ADAPTER = TypeAdapter(SessionEntry)


class SessionFormatError(ValueError):
    """Raised when persisted session data violates the canonical JSONL contract."""


def append_run_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    prompt: str,
    events: Sequence[RunEvent],
    messages: Sequence[ModelMessage],
) -> None:
    run_events = list(events)
    run_messages = list(messages)
    run_record = SessionRunRecord(
        run_id=_extract_run_id(run_events),
        prompt=prompt,
        messages=run_messages,
        events=run_events,
    )
    run_id = _validate_run_record(run_record)
    normalized_workspace_root = normalize_workspace_root(workspace_root)

    if path.exists():
        load_session(path=path, workspace_root=normalized_workspace_root)
        should_write_header = False
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        should_write_header = True

    with path.open("a", encoding="utf-8") as file_handle:
        if should_write_header:
            _write_entry(
                file_handle,
                SessionHeaderEntry(workspace_root=str(normalized_workspace_root)),
            )

        _write_entry(file_handle, SessionRunEntry(run_id=run_id, prompt=prompt))
        _write_entry(
            file_handle,
            SessionMessagesEntry(run_id=run_id, messages=run_messages),
        )
        for event in run_events:
            _write_entry(
                file_handle,
                SessionEventEntry(run_id=run_id, event=event),
            )


def load_session(
    *,
    path: Path,
    workspace_root: Path | str,
) -> LoadedSession:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise

    if not lines:
        raise SessionFormatError("Session file is empty")

    header: SessionHeaderEntry | None = None
    runs: list[SessionRunRecord] = []
    current_run: SessionRunRecord | None = None
    current_run_has_messages = False
    known_run_ids: set[str] = set()
    expected_workspace_root = str(normalize_workspace_root(workspace_root))

    for line_number, raw_line in enumerate(lines, start=1):
        entry = _parse_entry(raw_line=raw_line, line_number=line_number)

        if isinstance(entry, SessionHeaderEntry):
            if line_number != 1 or header is not None:
                raise SessionFormatError(
                    "Session header must be first and appear only once"
                )
            header = entry
            if header.workspace_root != expected_workspace_root:
                raise SessionFormatError(
                    "Session workspace_root mismatch: "
                    f"expected {expected_workspace_root}, got "
                    f"{header.workspace_root}"
                )
            continue

        if header is None:
            raise SessionFormatError("Session header must be first entry")

        if isinstance(entry, SessionRunEntry):
            if current_run is not None and not current_run_has_messages:
                raise SessionFormatError(
                    "session_run must be followed by exactly one session_messages entry"
                )
            if entry.run_id in known_run_ids:
                raise SessionFormatError(f"Duplicate session run_id: {entry.run_id}")

            current_run = SessionRunRecord(
                run_id=entry.run_id,
                prompt=entry.prompt,
                messages=[],
                events=[],
            )
            current_run_has_messages = False
            known_run_ids.add(entry.run_id)
            runs.append(current_run)
            continue

        if isinstance(entry, SessionMessagesEntry):
            if current_run is None:
                raise SessionFormatError(
                    "Session messages entry must follow a session_run entry"
                )
            if current_run_has_messages:
                raise SessionFormatError(
                    "session_run must be followed by exactly one session_messages entry"
                )
            if entry.run_id != current_run.run_id:
                raise SessionFormatError(
                    "Session messages entry must belong to the current run"
                )
            current_run.messages.extend(entry.messages)
            current_run_has_messages = True
            continue

        if current_run is None:
            raise SessionFormatError(
                "Session event entry must follow a session_run entry"
            )
        if not current_run_has_messages:
            raise SessionFormatError(
                "session_run must be followed by exactly one session_messages entry"
            )

        if entry.run_id != current_run.run_id:
            raise SessionFormatError(
                "Session event entry must belong to the current run"
            )

        if entry.event.run_id != entry.run_id:
            raise SessionFormatError("Session event run_id must match entry run_id")

        current_run.events.append(entry.event)

    assert header is not None
    if current_run is not None and not current_run_has_messages:
        raise SessionFormatError(
            "session_run must be followed by exactly one session_messages entry"
        )

    for run in runs:
        _validate_run_record(run)

    return LoadedSession(header=header, runs=runs)


def _extract_run_id(events: Sequence[RunEvent]) -> str:
    if not events:
        raise SessionFormatError("Run must contain at least one event")

    run_id = events[0].run_id
    if not run_id:
        raise SessionFormatError("Run event run_id must be non-empty")

    return run_id


def _validate_run_record(run: SessionRunRecord) -> str:
    if not run.events:
        raise SessionFormatError("Run must contain at least one event")

    first_event = run.events[0]
    if not isinstance(first_event, RunStartedEvent):
        raise SessionFormatError("Run must start with run_started")

    terminal_seen = False
    pending_tool_calls: dict[str, str] = {}

    for event in run.events:
        if event.run_id != run.run_id:
            raise SessionFormatError(
                "Persisted run event run_id must match session run_id"
            )

        if terminal_seen:
            raise SessionFormatError(
                "Run cannot contain events after the terminal outcome"
            )

        if isinstance(event, RunStartedEvent):
            if event is not first_event:
                raise SessionFormatError("run_started may appear only once per run")
            continue

        if isinstance(event, ToolCallStartedEvent):
            if event.tool_call_id in pending_tool_calls:
                raise SessionFormatError("Tool call IDs must be unique until resolved")
            pending_tool_calls[event.tool_call_id] = event.tool_name
            continue

        if isinstance(event, ToolCallSucceededEvent | ToolCallFailedEvent):
            expected_name = pending_tool_calls.pop(event.tool_call_id, None)
            if expected_name is None:
                raise SessionFormatError("Tool result must follow tool_call_started")
            if expected_name != event.tool_name:
                raise SessionFormatError(
                    "Tool result tool_name must match the started tool call"
                )
            continue

        if isinstance(event, RunSucceededEvent | RunFailedEvent):
            if pending_tool_calls:
                raise SessionFormatError(
                    "Run cannot terminate with unresolved tool calls"
                )
            terminal_seen = True

    if not terminal_seen:
        raise SessionFormatError("Run must end with a terminal outcome")

    return run.run_id


def _parse_entry(*, raw_line: str, line_number: int) -> SessionEntry:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError as error:
        raise SessionFormatError(f"Invalid JSON on line {line_number}") from error

    if (
        isinstance(payload, dict)
        and payload.get("type") == "session_header"
        and payload.get("version") != SESSION_FORMAT_VERSION
    ):
        raise SessionFormatError(
            f"Unsupported session format version on line {line_number}: "
            f"{payload.get('version')}"
        )

    try:
        return _SESSION_ENTRY_ADAPTER.validate_python(payload)
    except ValidationError as error:
        raise SessionFormatError(
            f"Invalid session entry on line {line_number}"
        ) from error


def _write_entry(
    file_handle: TextIO,
    entry: (
        SessionHeaderEntry
        | SessionRunEntry
        | SessionMessagesEntry
        | SessionEventEntry
    ),
) -> None:
    file_handle.write(entry.model_dump_json())
    file_handle.write("\n")
