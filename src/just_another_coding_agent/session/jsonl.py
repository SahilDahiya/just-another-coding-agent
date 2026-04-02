from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunSucceededEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.contracts.session import (
    SESSION_FORMAT_VERSION,
    LoadedSession,
    SessionCompactionEntry,
    SessionCompactionSummary,
    SessionEntry,
    SessionEventEntry,
    SessionForkEntry,
    SessionHeaderEntry,
    SessionInfoEntry,
    SessionMessagesEntry,
    SessionMetadata,
    SessionName,
    SessionRunEntry,
    SessionRunRecord,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.session.checkpoint import (
    build_compaction_checkpoint_messages,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root

_SESSION_ENTRY_ADAPTER = TypeAdapter(SessionEntry)
_SESSION_NAME_ADAPTER = TypeAdapter(SessionName)
_UNSET = object()


class SessionFormatError(ValueError):
    """Raised when persisted session data violates the canonical JSONL contract."""


class SessionNameValidationError(ValueError):
    """Raised when a requested session name cannot be normalized safely."""


class SessionRunAppender:
    """Append one run incrementally to the canonical session JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        workspace_root: Path | str,
        shell_family: ShellFamily | None = None,
        run_id: str,
        prompt: str,
        thinking: ThinkingSetting | None = None,
    ) -> None:
        self._path = path
        self._workspace_root = normalize_workspace_root(workspace_root)
        self._shell_family = shell_family or detect_default_shell_family()
        self._run_id = run_id
        self._events: list[RunEvent] = []
        self._finalized = False

        _ensure_session_is_appendable(
            path=self._path,
            workspace_root=self._workspace_root,
            shell_family=self._shell_family,
        )
        _append_entry_to_path(
            self._path,
            SessionRunEntry(run_id=run_id, prompt=prompt, thinking=thinking),
        )

    def append_event(self, event: RunEvent) -> None:
        if self._finalized:
            raise RuntimeError("Cannot append events after session run finalization")
        if event.run_id != self._run_id:
            raise SessionFormatError(
                "Persisted run event run_id must match session run_id"
            )

        candidate_events = [*self._events, event]
        _validate_run_events(
            run_id=self._run_id,
            events=candidate_events,
            require_terminal=False,
        )
        self._events = candidate_events
        _append_entry_to_path(
            self._path,
            SessionEventEntry(run_id=self._run_id, event=event),
        )

    def finalize(self, *, messages: Sequence[ModelMessage]) -> None:
        if self._finalized:
            raise RuntimeError("Session run already finalized")

        run_record = SessionRunRecord(
            run_id=self._run_id,
            prompt="",
            thinking=None,
            messages=list(messages),
            events=list(self._events),
        )
        _validate_run_record(run_record)
        _append_entry_to_path(
            self._path,
            SessionMessagesEntry(run_id=self._run_id, messages=list(messages)),
        )
        _update_session_metadata(path=self._path, updated_at=_utc_now())
        self._finalized = True


def initialize_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
) -> None:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    normalized_shell_family = shell_family or detect_default_shell_family()
    if path.exists():
        raise FileExistsError(f"Session already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        _write_entry(
            file_handle,
            SessionHeaderEntry(
                workspace_root=str(normalized_workspace_root),
                shell_family=normalized_shell_family,
            ),
        )
        _flush_file_handle(file_handle)
    timestamp = _utc_now()
    _write_session_metadata(
        path=_metadata_path_for_session_path(path),
        metadata=SessionMetadata(
            session_id=path.stem,
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def append_run_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    prompt: str,
    thinking: ThinkingSetting | None = None,
    events: Sequence[RunEvent],
    messages: Sequence[ModelMessage],
) -> None:
    run_events = list(events)
    run_messages = list(messages)
    run_id = _extract_run_id(run_events)
    appender = SessionRunAppender(
        path=path,
        workspace_root=workspace_root,
        shell_family=shell_family,
        run_id=run_id,
        prompt=prompt,
        thinking=thinking,
    )
    for event in run_events:
        appender.append_event(event)
    appender.finalize(messages=run_messages)


def append_compaction_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    summary: SessionCompactionSummary,
    summarized_through_run_id: str | None = None,
    first_kept_run_id: str | None = None,
    checkpoint_messages: list[ModelMessage] | None = None,
) -> SessionCompactionEntry:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    loaded = load_session(
        path=path,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
    )

    if not loaded.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    resolved_summarized_through_run_id = (
        summarized_through_run_id
        if summarized_through_run_id is not None
        else loaded.runs[-1].run_id
    )
    resolved_checkpoint_through_run_id = loaded.runs[-1].run_id
    retained_start_index = (
        _run_index_for_id(loaded, first_kept_run_id)
        if first_kept_run_id is not None
        else len(loaded.runs)
    )
    resolved_checkpoint_messages = (
        list(checkpoint_messages)
        if checkpoint_messages is not None
        else build_compaction_checkpoint_messages(
            summary=summary,
            retained_runs=loaded.runs[retained_start_index:],
        )
    )
    if not resolved_checkpoint_messages:
        raise SessionFormatError(
            "Compaction checkpoint must include at least one message"
        )

    entry = SessionCompactionEntry(
        compaction_id=uuid4().hex,
        summarized_through_run_id=resolved_summarized_through_run_id,
        first_kept_run_id=first_kept_run_id,
        checkpoint_through_run_id=resolved_checkpoint_through_run_id,
        checkpoint_messages=resolved_checkpoint_messages,
        summary=summary,
    )

    with path.open("a", encoding="utf-8") as file_handle:
        _write_entry(file_handle, entry)
        _flush_file_handle(file_handle)
    _update_session_metadata(path=path, updated_at=_utc_now())

    return entry


def append_session_name_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    name: str,
) -> SessionName:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    load_session(
        path=path,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
    )
    normalized_name = normalize_session_name(name)
    metadata = read_session_metadata(path=_metadata_path_for_session_path(path))
    if metadata.name == normalized_name:
        return normalized_name

    for candidate in _iter_workspace_session_metadata(path.parent):
        if candidate.session_id == metadata.session_id:
            continue
        if candidate.name == normalized_name:
            raise SessionNameValidationError(
                "Session name already in use in this workspace: "
                f"{normalized_name}"
            )

    entry = SessionInfoEntry(name=normalized_name)
    with path.open("a", encoding="utf-8") as file_handle:
        _write_entry(file_handle, entry)
        _flush_file_handle(file_handle)
    _update_session_metadata(
        path=path,
        name=normalized_name,
        updated_at=_utc_now(),
    )
    return normalized_name


def update_session_auto_compaction_failures(
    *,
    path: Path,
    consecutive_auto_compaction_failures: int,
) -> SessionMetadata:
    if consecutive_auto_compaction_failures < 0:
        raise ValueError("Auto-compaction failure count must be non-negative")
    return _update_session_metadata(
        path=path,
        consecutive_auto_compaction_failures=consecutive_auto_compaction_failures,
    )


def fork_session(
    *,
    source_path: Path,
    target_path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    forked_from_session_id: str,
) -> None:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    loaded_source = load_session(
        path=source_path,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
    )
    if target_path.exists():
        raise FileExistsError(f"Session already exists: {target_path}")

    source_entries = _read_entries(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as file_handle:
        _write_entry(
            file_handle,
            SessionHeaderEntry(
                workspace_root=str(normalized_workspace_root),
                shell_family=loaded_source.header.shell_family,
            ),
        )
        _write_entry(
            file_handle,
            SessionForkEntry(
                forked_from_session_id=forked_from_session_id,
                forked_from_run_id=(
                    loaded_source.runs[-1].run_id if loaded_source.runs else None
                ),
            ),
        )
        for entry in source_entries[1:]:
            if isinstance(
                entry,
                (SessionHeaderEntry, SessionForkEntry, SessionInfoEntry),
            ):
                continue
            _write_entry(file_handle, entry)
        _flush_file_handle(file_handle)

    timestamp = _utc_now()
    _write_session_metadata(
        path=_metadata_path_for_session_path(target_path),
        metadata=SessionMetadata(
            session_id=target_path.stem,
            created_at=timestamp,
            updated_at=timestamp,
            forked_from_session_id=forked_from_session_id,
        ),
    )


def load_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
) -> LoadedSession:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise

    if not lines:
        raise SessionFormatError("Session file is empty")

    header: SessionHeaderEntry | None = None
    fork: SessionForkEntry | None = None
    name: SessionName | None = None
    runs: list[SessionRunRecord] = []
    compactions: list[SessionCompactionEntry] = []
    current_run: SessionRunRecord | None = None
    known_run_ids: set[str] = set()
    run_order: list[str] = []
    latest_compaction_run_index = -1
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

        if isinstance(entry, SessionForkEntry):
            if current_run is not None:
                raise SessionFormatError(
                    "Session fork entry must not appear inside an incomplete run"
                )
            if fork is not None or runs or compactions or name is not None:
                raise SessionFormatError(
                    "Session fork entry must appear once immediately after header"
                )
            fork = entry
            continue

        if isinstance(entry, SessionRunEntry):
            if current_run is not None:
                raise SessionFormatError("Session ended with incomplete run")
            if entry.run_id in known_run_ids:
                raise SessionFormatError(f"Duplicate session run_id: {entry.run_id}")

            current_run = SessionRunRecord(
                run_id=entry.run_id,
                prompt=entry.prompt,
                thinking=entry.thinking,
                messages=[],
                events=[],
            )
            known_run_ids.add(entry.run_id)
            continue

        if isinstance(entry, SessionInfoEntry):
            if current_run is not None:
                raise SessionFormatError(
                    "Session info entry must not appear inside an incomplete run"
                )
            name = entry.name
            continue

        if isinstance(entry, SessionMessagesEntry):
            if current_run is None:
                raise SessionFormatError(
                    "Session messages entry must follow a session_run entry"
                )
            if entry.run_id != current_run.run_id:
                raise SessionFormatError(
                    "Session messages entry must belong to the current run"
                )
            current_run.messages.extend(entry.messages)
            _validate_run_record(current_run)
            runs.append(current_run)
            run_order.append(current_run.run_id)
            current_run = None
            continue

        if isinstance(entry, SessionCompactionEntry):
            if current_run is not None:
                raise SessionFormatError(
                    "Session compaction entry must follow at least one complete run"
                )
            elif not runs:
                raise SessionFormatError(
                    "Session compaction entry must follow at least one complete run"
                )

            try:
                compaction_run_index = run_order.index(entry.summarized_through_run_id)
            except ValueError as error:
                raise SessionFormatError(
                    "Session compaction entry must reference an existing run_id"
                ) from error

            if entry.first_kept_run_id is not None:
                try:
                    first_kept_run_index = run_order.index(entry.first_kept_run_id)
                except ValueError as error:
                    raise SessionFormatError(
                        "Session compaction kept boundary must reference "
                        "an existing run_id"
                    ) from error

                if first_kept_run_index < compaction_run_index:
                    raise SessionFormatError(
                        "Session compaction kept boundary must not precede "
                        "the summary boundary"
                    )

            try:
                checkpoint_run_index = run_order.index(
                    entry.checkpoint_through_run_id
                )
            except ValueError as error:
                raise SessionFormatError(
                    "Session compaction checkpoint must reference an existing run_id"
                ) from error

            if checkpoint_run_index < compaction_run_index:
                raise SessionFormatError(
                    "Session compaction checkpoint must not precede "
                    "the summary boundary"
                )

            if (
                entry.first_kept_run_id is not None
                and checkpoint_run_index < first_kept_run_index
            ):
                raise SessionFormatError(
                    "Session compaction checkpoint must include the kept boundary"
                )

            if compaction_run_index < latest_compaction_run_index:
                raise SessionFormatError(
                    "Session compaction entries must not move the summary "
                    "boundary backward"
                )

            latest_compaction_run_index = compaction_run_index
            compactions.append(entry)
            continue

        if current_run is None:
            raise SessionFormatError(
                "Session event entry must follow a session_run entry"
            )
        if entry.run_id != current_run.run_id:
            raise SessionFormatError(
                "Session event entry must belong to the current run"
            )

        if entry.event.run_id != entry.run_id:
            raise SessionFormatError("Session event run_id must match entry run_id")

        current_run.events.append(entry.event)
        _validate_run_events(
            run_id=current_run.run_id,
            events=current_run.events,
            require_terminal=False,
        )

    if header is None:
        raise SessionFormatError("Session header must be first entry")
    if current_run is not None:
        raise SessionFormatError("Session ended with incomplete run")

    return LoadedSession(
        header=header,
        fork=fork,
        name=name,
        runs=runs,
        compactions=compactions,
    )


def read_session_metadata(*, path: Path) -> SessionMetadata:
    try:
        raw_payload = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SessionFormatError(f"Session metadata is missing: {path}") from None

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise SessionFormatError(f"Invalid session metadata JSON: {path}") from error

    try:
        return SessionMetadata.model_validate(payload)
    except ValidationError as error:
        raise SessionFormatError(f"Invalid session metadata: {path}") from error


def _extract_run_id(events: Sequence[RunEvent]) -> str:
    if not events:
        raise SessionFormatError("Run must contain at least one event")

    run_id = events[0].run_id
    if not run_id:
        raise SessionFormatError("Run event run_id must be non-empty")

    return run_id


def _validate_run_record(run: SessionRunRecord) -> str:
    _validate_run_events(run_id=run.run_id, events=run.events, require_terminal=True)
    _validate_run_messages(run.messages)
    return run.run_id


def _validate_run_events(
    *,
    run_id: str,
    events: Sequence[RunEvent],
    require_terminal: bool,
) -> None:
    if not events:
        raise SessionFormatError("Run must contain at least one event")

    first_event = events[0]
    if not isinstance(first_event, RunStartedEvent):
        raise SessionFormatError("Run must start with run_started")

    terminal_seen = False
    pending_tool_calls: dict[str, str] = {}

    for event in events:
        if event.run_id != run_id:
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

        if isinstance(event, ToolCallUpdatedEvent):
            expected_name = pending_tool_calls.get(event.tool_call_id)
            if expected_name is None:
                raise SessionFormatError("Tool update must follow tool_call_started")
            if expected_name != event.tool_name:
                raise SessionFormatError(
                    "Tool update tool_name must match the started tool call"
                )
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
        if require_terminal:
            raise SessionFormatError("Run must end with a terminal outcome")


def _validate_run_messages(messages: Sequence[ModelMessage]) -> None:
    pending_tool_calls: dict[str, str] = {}

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                if part.tool_call_id in pending_tool_calls:
                    raise SessionFormatError(
                        "Session messages must not reuse tool_call_id before a "
                        "matching tool return"
                    )
                pending_tool_calls[part.tool_call_id] = part.tool_name
                continue

            if isinstance(part, ToolReturnPart):
                expected_name = pending_tool_calls.get(part.tool_call_id)
                if expected_name is None:
                    continue
                if expected_name != part.tool_name:
                    raise SessionFormatError(
                        "Session message tool return tool_name must match the tool call"
                    )
                pending_tool_calls.pop(part.tool_call_id, None)
                continue

            if isinstance(part, RetryPromptPart):
                pending_tool_calls.pop(part.tool_call_id, None)

    if pending_tool_calls:
        raise SessionFormatError(
            "Session messages cannot contain unresolved tool calls"
        )


def _run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise SessionFormatError(f"Unknown session run_id: {run_id}")


def start_run_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    run_id: str,
    prompt: str,
    thinking: ThinkingSetting | None = None,
) -> SessionRunAppender:
    return SessionRunAppender(
        path=path,
        workspace_root=workspace_root,
        shell_family=shell_family,
        run_id=run_id,
        prompt=prompt,
        thinking=thinking,
    )


def normalize_session_name(name: str) -> SessionName:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not normalized:
        raise SessionNameValidationError(
            "Session name must contain at least one letter or number"
        )
    return _SESSION_NAME_ADAPTER.validate_python(normalized)


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


def _read_entries(path: Path) -> list[SessionEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SessionFormatError("Session file is empty")
    return [
        _parse_entry(raw_line=raw_line, line_number=line_number)
        for line_number, raw_line in enumerate(lines, start=1)
    ]


def _write_entry(
    file_handle: TextIO,
    entry: (
        SessionHeaderEntry
        | SessionForkEntry
        | SessionInfoEntry
        | SessionRunEntry
        | SessionMessagesEntry
        | SessionEventEntry
        | SessionCompactionEntry
    ),
) -> None:
    file_handle.write(entry.model_dump_json())
    file_handle.write("\n")


def _append_entry_to_path(
    path: Path,
    entry: (
        SessionHeaderEntry
        | SessionForkEntry
        | SessionInfoEntry
        | SessionRunEntry
        | SessionMessagesEntry
        | SessionEventEntry
        | SessionCompactionEntry
    ),
) -> None:
    with path.open("a", encoding="utf-8") as file_handle:
        _write_entry(file_handle, entry)
        _flush_file_handle(file_handle)


def _ensure_session_is_appendable(
    *,
    path: Path,
    workspace_root: Path,
    shell_family: ShellFamily,
) -> None:
    if path.exists():
        load_session(
            path=path,
            workspace_root=workspace_root,
            shell_family=shell_family,
        )
        read_session_metadata(path=_metadata_path_for_session_path(path))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        _write_entry(
            file_handle,
            SessionHeaderEntry(
                workspace_root=str(workspace_root),
                shell_family=shell_family,
            ),
        )
        _flush_file_handle(file_handle)
    timestamp = _utc_now()
    _write_session_metadata(
        path=_metadata_path_for_session_path(path),
        metadata=SessionMetadata(
            session_id=path.stem,
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def _flush_file_handle(file_handle: TextIO) -> None:
    file_handle.flush()
    os.fsync(file_handle.fileno())


def _metadata_path_for_session_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _write_session_metadata(*, path: Path, metadata: SessionMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(metadata.model_dump_json(), encoding="utf-8")


def _update_session_metadata(
    *,
    path: Path,
    name: SessionName | None | object = _UNSET,
    updated_at: datetime | object = _UNSET,
    consecutive_auto_compaction_failures: int | object = _UNSET,
) -> SessionMetadata:
    metadata_path = _metadata_path_for_session_path(path)
    existing = read_session_metadata(path=metadata_path)
    metadata = existing.model_copy(
        update={
            key: value
            for key, value in {
                "name": name,
                "updated_at": updated_at,
                "consecutive_auto_compaction_failures": (
                    consecutive_auto_compaction_failures
                ),
            }.items()
            if value is not _UNSET
        }
    )
    _write_session_metadata(path=metadata_path, metadata=metadata)
    return metadata


def _iter_workspace_session_metadata(
    workspace_sessions_root: Path,
) -> Sequence[SessionMetadata]:
    return [
        read_session_metadata(path=metadata_path)
        for metadata_path in sorted(workspace_sessions_root.glob("*.meta.json"))
    ]


def _utc_now() -> datetime:
    return datetime.now(UTC)
