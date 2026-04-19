from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.run_events import (
    AssistantTextDeltaEvent,
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
    SessionEntry,
    SessionEventEntry,
    SessionForkEntry,
    SessionHeaderEntry,
    SessionInfoEntry,
    SessionMessagesEntry,
    SessionMetadata,
    SessionName,
    SessionProjectDocReference,
    SessionProjectDocsEntry,
    SessionRunEntry,
    SessionRunRecord,
    SessionTurnContextEntry,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.project_docs import (
    load_workspace_project_docs,
)
from just_another_coding_agent.session.replacement_history import (
    validate_compaction_replacement_messages,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root

_SESSION_ENTRY_ADAPTER = TypeAdapter(SessionEntry)
_SESSION_NAME_ADAPTER = TypeAdapter(SessionName)
_UNSET = object()


class SessionFormatError(ValueError):
    """Raised when persisted session data violates the canonical JSONL contract."""


class SessionNameValidationError(ValueError):
    """Raised when a requested session name cannot be normalized safely."""


@dataclass
class _AppenderValidatorState:
    saw_run_started: bool = False
    pending_tool_calls: dict[str, str] = field(default_factory=dict)
    terminal_seen: bool = False


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
        self._file: TextIO | None = None
        self._validator_state = _AppenderValidatorState()

        _ensure_session_is_appendable(
            path=self._path,
            workspace_root=self._workspace_root,
            shell_family=self._shell_family,
        )
        self._file = self._path.open("a", encoding="utf-8")
        try:
            _write_entry(
                self._file,
                SessionRunEntry(run_id=run_id, prompt=prompt, thinking=thinking),
            )
            _flush_file_handle(self._file, sync_to_disk=False)
        except Exception:
            self.close()
            raise

    def append_event(self, event: RunEvent) -> None:
        if self._finalized:
            raise RuntimeError("Cannot append events after session run finalization")
        if event.run_id != self._run_id:
            raise SessionFormatError(
                "Persisted run event run_id must match session run_id"
            )

        if isinstance(event, AssistantTextDeltaEvent):
            return

        file_handle = self._require_open_file()
        _validate_run_event_incremental(
            state=self._validator_state,
            event=event,
            run_id=self._run_id,
        )
        self._events.append(event)
        _write_entry(
            file_handle,
            SessionEventEntry(run_id=self._run_id, event=event),
        )
        _flush_file_handle(file_handle, sync_to_disk=False)

    def finalize(
        self,
        *,
        messages: Sequence[ModelMessage],
        turn_context: SessionTurnContextEntry | None = None,
    ) -> None:
        if self._finalized:
            raise RuntimeError("Session run already finalized")
        file_handle = self._require_open_file()
        try:
            run_record = SessionRunRecord(
                run_id=self._run_id,
                prompt="",
                thinking=None,
                messages=list(messages),
                events=list(self._events),
            )
            _validate_run_record(run_record)
            _write_entry(
                file_handle,
                SessionMessagesEntry(run_id=self._run_id, messages=list(messages)),
            )
            if turn_context is not None:
                if turn_context.run_id != self._run_id:
                    raise SessionFormatError(
                        "Session turn context entry run_id must belong to "
                        "the current run"
                    )
                if turn_context.workspace_root != str(self._workspace_root):
                    raise SessionFormatError(
                        "Session turn context workspace_root must match "
                        "session workspace_root"
                    )
                if turn_context.shell_family != self._shell_family:
                    raise SessionFormatError(
                        "Session turn context shell_family must match "
                        "session shell_family"
                    )
                _write_entry(file_handle, turn_context)
            _flush_file_handle(file_handle, sync_to_disk=True)
            _update_session_metadata(path=self._path, updated_at=_utc_now())
            self._finalized = True
        finally:
            self.close()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _require_open_file(self) -> TextIO:
        if self._file is None:
            raise RuntimeError("Session run appender is closed")
        return self._file


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


def append_project_docs_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    project_docs_root: Path | str | None = None,
    shell_family: ShellFamily | None = None,
) -> SessionProjectDocsEntry | None:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    normalized_project_docs_root = normalize_workspace_root(
        project_docs_root
        if project_docs_root is not None
        else normalized_workspace_root
    )
    load_session(
        path=path,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
    )
    loaded_docs = load_workspace_project_docs(normalized_project_docs_root)
    if not loaded_docs:
        return None

    entry = SessionProjectDocsEntry(
        documents=[
            SessionProjectDocReference(
                short_path=doc.short_path,
                truncated=doc.truncated,
            )
            for doc in loaded_docs
        ]
    )
    _append_entry_to_path(path, entry)
    _update_session_metadata(path=path, updated_at=_utc_now())
    return entry


def append_run_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    prompt: str,
    thinking: ThinkingSetting | None = None,
    events: Sequence[RunEvent],
    messages: Sequence[ModelMessage],
    turn_context: SessionTurnContextEntry | None = None,
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
    appender.finalize(messages=run_messages, turn_context=turn_context)


def append_compaction_to_session(
    *,
    path: Path,
    workspace_root: Path | str,
    shell_family: ShellFamily | None = None,
    replacement_messages: list[ModelMessage],
    compacted_through_run_id: str | None = None,
) -> SessionCompactionEntry:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    loaded = load_session(
        path=path,
        workspace_root=normalized_workspace_root,
        shell_family=shell_family,
    )

    if not loaded.runs:
        raise SessionFormatError("Cannot compact a session with no completed runs")

    resolved_compacted_through_run_id = (
        compacted_through_run_id
        if compacted_through_run_id is not None
        else loaded.runs[-1].run_id
    )
    try:
        validate_compaction_replacement_messages(replacement_messages)
    except ValueError as error:
        raise SessionFormatError(str(error)) from error
    _validate_run_messages(replacement_messages)

    try:
        resolved_compaction_run_index = _run_index_for_id(
            loaded,
            resolved_compacted_through_run_id,
        )
    except SessionFormatError as error:
        raise SessionFormatError(
            "Session compaction entry must reference an existing run_id"
        ) from error
    latest_compaction = loaded.latest_compaction
    if latest_compaction is not None:
        latest_compaction_run_index = _run_index_for_id(
            loaded,
            latest_compaction.compacted_through_run_id,
        )
        if resolved_compaction_run_index < latest_compaction_run_index:
            raise SessionFormatError(
                "Session compaction entries must not move the compaction "
                "boundary backward"
            )

    entry = SessionCompactionEntry(
        compaction_id=uuid4().hex,
        compacted_through_run_id=resolved_compacted_through_run_id,
        replacement_messages=list(replacement_messages),
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
                (
                    SessionHeaderEntry,
                    SessionForkEntry,
                    SessionInfoEntry,
                    SessionTurnContextEntry,
                ),
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
    project_docs: SessionProjectDocsEntry | None = None
    runs: list[SessionRunRecord] = []
    latest_turn_context: SessionTurnContextEntry | None = None
    has_persisted_turn_context_history = False
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

        if isinstance(entry, SessionProjectDocsEntry):
            if current_run is not None:
                raise SessionFormatError(
                    "Session project docs entry must not appear "
                    "inside an incomplete run"
                )
            if project_docs is not None or runs or compactions:
                raise SessionFormatError(
                    "Session project docs entry must appear at most once before runs"
                )
            project_docs = entry
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

        if isinstance(entry, SessionTurnContextEntry):
            if current_run is not None:
                raise SessionFormatError(
                    "Session turn context entry must follow a complete run"
                )
            if not runs:
                raise SessionFormatError(
                    "Session turn context entry must follow a complete run"
                )
            if entry.run_id != runs[-1].run_id:
                raise SessionFormatError(
                    "Session turn context entry must belong to the latest complete run"
                )
            if entry.workspace_root != header.workspace_root:
                raise SessionFormatError(
                    "Session turn context workspace_root must match "
                    "session workspace_root"
                )
            if (
                has_persisted_turn_context_history
                and latest_turn_context is not None
                and latest_turn_context.run_id == entry.run_id
            ):
                raise SessionFormatError(
                    "Session turn context entry must appear at most once per run"
                )
            has_persisted_turn_context_history = True
            latest_turn_context = entry
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
                compaction_run_index = run_order.index(entry.compacted_through_run_id)
            except ValueError as error:
                raise SessionFormatError(
                    "Session compaction entry must reference an existing run_id"
                ) from error

            try:
                validate_compaction_replacement_messages(entry.replacement_messages)
            except ValueError as error:
                raise SessionFormatError(str(error)) from error
            _validate_run_messages(entry.replacement_messages)

            if compaction_run_index < latest_compaction_run_index:
                raise SessionFormatError(
                    "Session compaction entries must not move the compaction "
                    "boundary backward"
                )

            latest_compaction_run_index = compaction_run_index
            compactions.append(entry)
            latest_turn_context = None
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
        project_docs=project_docs,
        runs=runs,
        latest_turn_context=latest_turn_context,
        has_persisted_turn_context_history=has_persisted_turn_context_history,
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


def _validate_run_event_incremental(
    *,
    state: _AppenderValidatorState,
    event: RunEvent,
    run_id: str,
) -> None:
    if event.run_id != run_id:
        raise SessionFormatError(
            "Persisted run event run_id must match session run_id"
        )

    if state.terminal_seen:
        raise SessionFormatError(
            "Run cannot contain events after the terminal outcome"
        )

    if isinstance(event, RunStartedEvent):
        if state.saw_run_started:
            raise SessionFormatError("run_started may appear only once per run")
        state.saw_run_started = True
        return

    if not state.saw_run_started:
        raise SessionFormatError("Run must start with run_started")

    if isinstance(event, ToolCallStartedEvent):
        if event.tool_call_id in state.pending_tool_calls:
            raise SessionFormatError("Tool call IDs must be unique until resolved")
        state.pending_tool_calls[event.tool_call_id] = event.tool_name
        return

    if isinstance(event, ToolCallUpdatedEvent):
        expected_name = state.pending_tool_calls.get(event.tool_call_id)
        if expected_name is None:
            raise SessionFormatError("Tool update must follow tool_call_started")
        if expected_name != event.tool_name:
            raise SessionFormatError(
                "Tool update tool_name must match the started tool call"
            )
        return

    if isinstance(event, ToolCallSucceededEvent | ToolCallFailedEvent):
        expected_name = state.pending_tool_calls.get(event.tool_call_id)
        if expected_name is None:
            raise SessionFormatError("Tool result must follow tool_call_started")
        if expected_name != event.tool_name:
            raise SessionFormatError(
                "Tool result tool_name must match the started tool call"
            )
        state.pending_tool_calls.pop(event.tool_call_id, None)
        return

    if isinstance(event, RunSucceededEvent | RunFailedEvent):
        if state.pending_tool_calls:
            raise SessionFormatError(
                "Run cannot terminate with unresolved tool calls"
            )
        state.terminal_seen = True


def _validate_run_messages(messages: Sequence[ModelMessage]) -> None:
    pending_tool_calls: dict[str, str] = {}

    for message in messages:
        if isinstance(message, ModelRequest):
            if message.instructions is not None:
                raise SessionFormatError(
                    "Session messages must not persist internal instructions"
                )
            if any(isinstance(part, SystemPromptPart) for part in message.parts):
                raise SessionFormatError(
                    "Session messages must not persist system prompt parts"
                )
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
        | SessionProjectDocsEntry
        | SessionRunEntry
        | SessionMessagesEntry
        | SessionEventEntry
        | SessionTurnContextEntry
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
        | SessionProjectDocsEntry
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


def _flush_file_handle(file_handle: TextIO, *, sync_to_disk: bool = True) -> None:
    file_handle.flush()
    if sync_to_disk:
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
