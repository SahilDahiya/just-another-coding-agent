from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.rpc import SessionId
from just_another_coding_agent.contracts.session import SessionHeaderEntry, SessionName
from just_another_coding_agent.session import (
    SessionFormatError,
    initialize_session,
    load_session,
    normalize_session_name,
)

_SESSION_ID_ADAPTER = TypeAdapter(SessionId)


class SessionLookupError(ValueError):
    """Raised when a requested session reference cannot be resolved uniquely."""


@dataclass(frozen=True)
class ResolvedSessionReference:
    session_id: str
    name: SessionName | None


def create_session(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> str:
    root = Path(sessions_root)
    while True:
        session_id = uuid4().hex
        session_path = session_path_for_id(
            sessions_root=root,
            session_id=session_id,
        )
        if session_path.exists():
            continue

        initialize_session(path=session_path, workspace_root=workspace_root)
        return session_id


def session_path_for_id(
    *,
    sessions_root: Path | str,
    session_id: str,
) -> Path:
    validated_session_id = _SESSION_ID_ADAPTER.validate_python(session_id)
    return Path(sessions_root) / f"{validated_session_id}.jsonl"


def resolve_session_reference(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_ref: str,
) -> ResolvedSessionReference:
    expected_workspace_root = str(Path(workspace_root).expanduser().resolve())
    try:
        validated_session_id = _SESSION_ID_ADAPTER.validate_python(session_ref)
    except ValidationError:
        validated_session_id = None

    if validated_session_id is not None:
        session_path = session_path_for_id(
            sessions_root=sessions_root,
            session_id=validated_session_id,
        )
        if not session_path.exists():
            raise SessionLookupError(f"Unknown session: {validated_session_id}")
        loaded = load_session(path=session_path, workspace_root=workspace_root)
        return ResolvedSessionReference(
            session_id=validated_session_id,
            name=loaded.name,
        )

    normalized_name = normalize_session_name(session_ref)
    matches: list[ResolvedSessionReference] = []
    for session_path in sorted(Path(sessions_root).glob("*.jsonl")):
        header = _load_session_header(session_path)
        if header.workspace_root != expected_workspace_root:
            continue
        loaded = load_session(path=session_path, workspace_root=workspace_root)
        if loaded.name == normalized_name:
            matches.append(
                ResolvedSessionReference(
                    session_id=session_path.stem,
                    name=loaded.name,
                )
            )

    if not matches:
        raise SessionLookupError(f"Unknown session: {normalized_name}")
    if len(matches) > 1:
        match_ids = ", ".join(match.session_id for match in matches)
        raise SessionLookupError(
            "Ambiguous session name: "
            f"{normalized_name}. Matching session ids: {match_ids}"
        )
    return matches[0]


def _load_session_header(path: Path) -> SessionHeaderEntry:
    with path.open("r", encoding="utf-8") as file_handle:
        first_line = file_handle.readline()
    if first_line == "":
        raise SessionFormatError("Session file is empty")

    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError as error:
        raise SessionFormatError("Invalid JSON on line 1") from error

    try:
        return SessionHeaderEntry.model_validate(payload)
    except ValidationError as error:
        raise SessionFormatError("Session header must be first entry") from error


__all__ = [
    "ResolvedSessionReference",
    "SessionLookupError",
    "create_session",
    "resolve_session_reference",
    "session_path_for_id",
]
