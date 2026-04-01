from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.rpc import SessionId
from just_another_coding_agent.contracts.session import SessionMetadata, SessionName
from just_another_coding_agent.session import (
    append_session_name_to_session,
    fork_session,
    initialize_session,
    normalize_session_name,
    read_session_metadata,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root

_SESSION_ID_ADAPTER = TypeAdapter(SessionId)


class SessionLookupError(ValueError):
    """Raised when a requested session reference cannot be resolved uniquely."""


@dataclass(frozen=True)
class ResolvedSessionReference:
    session_id: str
    name: SessionName | None
    forked_from_session_id: str | None = None


@dataclass(frozen=True)
class ListedSession:
    session_id: str
    name: SessionName | None
    created_at: datetime
    updated_at: datetime
    forked_from_session_id: str | None = None


def create_session(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> str:
    while True:
        session_id = uuid4().hex
        session_path = session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=session_id,
        )
        if session_path.exists():
            continue

        initialize_session(path=session_path, workspace_root=workspace_root)
        return session_id


def session_path_for_id(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str,
) -> Path:
    validated_session_id = _SESSION_ID_ADAPTER.validate_python(session_id)
    return workspace_sessions_dir(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    ) / f"{validated_session_id}.jsonl"


def resolve_session_reference(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_ref: str,
) -> ResolvedSessionReference:
    try:
        validated_session_id = _SESSION_ID_ADAPTER.validate_python(session_ref)
    except ValidationError:
        validated_session_id = None

    if validated_session_id is not None:
        session_path = session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=validated_session_id,
        )
        if not session_path.exists():
            raise SessionLookupError(f"Unknown session: {validated_session_id}")
        metadata = read_session_metadata(
            path=_metadata_path_for_session_path(session_path)
        )
        return ResolvedSessionReference(
            session_id=validated_session_id,
            name=metadata.name,
            forked_from_session_id=metadata.forked_from_session_id,
        )

    normalized_name = normalize_session_name(session_ref)
    matches = [
        ResolvedSessionReference(
            session_id=metadata.session_id,
            name=metadata.name,
            forked_from_session_id=metadata.forked_from_session_id,
        )
        for metadata in _iter_workspace_session_metadata(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
        if metadata.name == normalized_name
    ]
    if not matches:
        raise SessionLookupError(f"Unknown session: {normalized_name}")
    if len(matches) > 1:
        match_ids = ", ".join(match.session_id for match in matches)
        raise SessionLookupError(
            "Ambiguous session name: "
            f"{normalized_name}. Matching session ids: {match_ids}"
        )
    return matches[0]


def list_workspace_sessions(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> list[ListedSession]:
    sessions = [
        ListedSession(
            session_id=metadata.session_id,
            name=metadata.name,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
            forked_from_session_id=metadata.forked_from_session_id,
        )
        for metadata in _iter_workspace_session_metadata(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
    ]
    sessions.sort(key=lambda session: session.updated_at, reverse=True)
    return sessions


def create_fork(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    source_session_id: str,
    name: str | None = None,
) -> ResolvedSessionReference:
    source_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=source_session_id,
    )
    if not source_path.exists():
        raise SessionLookupError(f"Unknown session: {source_session_id}")
    normalized_name = normalize_session_name(name) if name is not None else None
    if normalized_name is not None:
        _ensure_workspace_session_name_available(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            normalized_name=normalized_name,
        )

    while True:
        session_id = uuid4().hex
        target_path = session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=session_id,
        )
        if target_path.exists():
            continue
        break

    fork_session(
        source_path=source_path,
        target_path=target_path,
        workspace_root=workspace_root,
        forked_from_session_id=source_session_id,
    )
    resolved_name: SessionName | None = None
    if normalized_name is not None:
        resolved_name = append_session_name_to_session(
            path=target_path,
            workspace_root=workspace_root,
            name=normalized_name,
        )
    return ResolvedSessionReference(
        session_id=session_id,
        name=resolved_name,
        forked_from_session_id=source_session_id,
    )


def workspace_sessions_dir(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> Path:
    return Path(sessions_root) / _workspace_key(workspace_root)


def _iter_workspace_session_metadata(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> list[SessionMetadata]:
    workspace_dir = workspace_sessions_dir(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    if not workspace_dir.exists():
        return []
    return [
        read_session_metadata(path=metadata_path)
        for metadata_path in sorted(workspace_dir.glob("*.meta.json"))
    ]


def _metadata_path_for_session_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _ensure_workspace_session_name_available(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    normalized_name: SessionName,
) -> None:
    for metadata in _iter_workspace_session_metadata(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    ):
        if metadata.name == normalized_name:
            raise SessionLookupError(
                f"Session name already in use in this workspace: {normalized_name}"
            )


def _workspace_key(workspace_root: Path | str) -> str:
    normalized_workspace_root = str(normalize_workspace_root(workspace_root))
    slug = re.sub(r"[^a-z0-9]+", "-", Path(normalized_workspace_root).name.lower())
    normalized_slug = slug.strip("-") or "workspace"
    digest = hashlib.sha256(normalized_workspace_root.encode("utf-8")).hexdigest()[:16]
    return f"{normalized_slug}-{digest}"


__all__ = [
    "ListedSession",
    "ResolvedSessionReference",
    "SessionLookupError",
    "create_fork",
    "create_session",
    "list_workspace_sessions",
    "resolve_session_reference",
    "session_path_for_id",
    "workspace_sessions_dir",
]
