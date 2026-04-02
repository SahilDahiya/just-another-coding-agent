from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.rpc import SessionId
from just_another_coding_agent.contracts.work_graph import (
    WorkItem,
    WorkItemId,
    WorkItemKind,
    WorkItemStatus,
    WorkSessionLink,
    WorkSlug,
    WorkUpdate,
    WorkUpdateKind,
)
from just_another_coding_agent.workspace_storage import workspace_key

DEFAULT_WORKSPACES_ROOT = Path.home() / ".jaca" / "workspaces"

_WORK_ITEM_ADAPTER = TypeAdapter(WorkItem)
_WORK_ITEM_ID_ADAPTER = TypeAdapter(WorkItemId)
_WORK_ITEM_KIND_ADAPTER = TypeAdapter(WorkItemKind)
_WORK_ITEM_STATUS_ADAPTER = TypeAdapter(WorkItemStatus)
_WORK_SESSION_LINK_ADAPTER = TypeAdapter(WorkSessionLink)
_WORK_SLUG_ADAPTER = TypeAdapter(WorkSlug)
_WORK_UPDATE_ADAPTER = TypeAdapter(WorkUpdate)
_WORK_UPDATE_KIND_ADAPTER = TypeAdapter(WorkUpdateKind)
_SESSION_ID_ADAPTER = TypeAdapter(SessionId)
_UNSET: Final = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('project', 'task')),
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('todo', 'in_progress', 'blocked', 'done', 'archived')
    ),
    parent_id TEXT NULL REFERENCES work_items(id),
    body_md TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT NULL
);

CREATE TABLE IF NOT EXISTS work_updates (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items(id),
    kind TEXT NOT NULL CHECK (
        kind IN ('note', 'decision', 'verification', 'status_change', 'completion')
    ),
    body_md TEXT NOT NULL,
    session_id TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_session_links (
    work_item_id TEXT NOT NULL REFERENCES work_items(id),
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (work_item_id, session_id)
);
"""


class WorkGraphError(ValueError):
    """Raised when work-graph state or input is invalid."""


class WorkItemLookupError(WorkGraphError):
    """Raised when a requested work item cannot be found."""


def workspace_work_graph_dir(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
) -> Path:
    return Path(workspaces_root) / workspace_key(workspace_root)


def work_graph_db_path(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
) -> Path:
    return workspace_work_graph_dir(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    ) / "work.sqlite"


def normalize_work_slug(value: str) -> WorkSlug:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise WorkGraphError(
            "Work item slug must contain at least one letter or number"
        )
    return _WORK_SLUG_ADAPTER.validate_python(normalized)


def create_work_item(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    kind: WorkItemKind,
    title: str,
    body_md: str = "",
    slug: str | None = None,
    parent_id: str | None = None,
) -> WorkItem:
    normalized_kind = _WORK_ITEM_KIND_ADAPTER.validate_python(kind)
    normalized_title = title.strip()
    if not normalized_title:
        raise WorkGraphError("Work item title must be non-empty")
    normalized_parent_id = (
        _validate_work_item_id(
            parent_id,
            message=f"Unknown parent work item: {parent_id}",
        )
        if parent_id is not None
        else None
    )
    normalized_slug = normalize_work_slug(slug or normalized_title)
    now = _utc_now()
    item_id = uuid4().hex
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )

    with _connect(db_path) as connection:
        if normalized_parent_id is not None:
            _require_existing_work_item(connection, work_item_id=normalized_parent_id)
        try:
            connection.execute(
                """
                INSERT INTO work_items (
                    id,
                    kind,
                    slug,
                    title,
                    status,
                    parent_id,
                    body_md,
                    created_at,
                    updated_at,
                    archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    normalized_kind,
                    normalized_slug,
                    normalized_title,
                    "todo",
                    normalized_parent_id,
                    body_md,
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                    None,
                ),
            )
        except sqlite3.IntegrityError as error:
            if "work_items.slug" in str(error):
                raise WorkGraphError(
                    "Work item slug already in use in this workspace: "
                    f"{normalized_slug}"
                ) from error
            raise
        return _load_work_item_by_id(connection, work_item_id=item_id)


def get_work_item_by_slug(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    slug: str,
) -> WorkItem:
    normalized_slug = normalize_work_slug(slug)
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                kind,
                slug,
                title,
                status,
                parent_id,
                body_md,
                created_at,
                updated_at,
                archived_at
            FROM work_items
            WHERE slug = ?
            """,
            (normalized_slug,),
        ).fetchone()
        if row is None:
            raise WorkItemLookupError(f"Unknown work item slug: {normalized_slug}")
        return _row_to_work_item(row)


def list_work_items(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    include_archived: bool = True,
) -> list[WorkItem]:
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        if include_archived:
            rows = connection.execute(
                """
                SELECT
                    id,
                    kind,
                    slug,
                    title,
                    status,
                    parent_id,
                    body_md,
                    created_at,
                    updated_at,
                    archived_at
                FROM work_items
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT
                    id,
                    kind,
                    slug,
                    title,
                    status,
                    parent_id,
                    body_md,
                    created_at,
                    updated_at,
                    archived_at
                FROM work_items
                WHERE status != 'archived'
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        return [_row_to_work_item(row) for row in rows]


def update_work_item(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    work_item_id: str,
    title: str | object = _UNSET,
    body_md: str | object = _UNSET,
    status: WorkItemStatus | object = _UNSET,
    parent_id: str | None | object = _UNSET,
) -> WorkItem:
    normalized_work_item_id = _validate_work_item_id(
        work_item_id,
        message=f"Unknown work item: {work_item_id}",
    )
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        existing = _load_work_item_by_id(
            connection,
            work_item_id=normalized_work_item_id,
        )

        updated_title = existing.title
        if title is not _UNSET:
            normalized_title = str(title).strip()
            if not normalized_title:
                raise WorkGraphError("Work item title must be non-empty")
            updated_title = normalized_title

        updated_body = existing.body_md if body_md is _UNSET else str(body_md)
        updated_status = (
            existing.status
            if status is _UNSET
            else _WORK_ITEM_STATUS_ADAPTER.validate_python(status)
        )
        updated_parent_id = existing.parent_id
        if parent_id is not _UNSET:
            updated_parent_id = (
                _validate_work_item_id(
                    parent_id,
                    message=f"Unknown parent work item: {parent_id}",
                )
                if parent_id is not None
                else None
            )
            if updated_parent_id == normalized_work_item_id:
                raise WorkGraphError("Work item cannot be its own parent")
            if updated_parent_id is not None:
                _require_existing_work_item(connection, work_item_id=updated_parent_id)

        if (
            updated_title == existing.title
            and updated_body == existing.body_md
            and updated_status == existing.status
            and updated_parent_id == existing.parent_id
        ):
            return existing

        now = _utc_now()
        archived_at = existing.archived_at
        if updated_status == "archived":
            archived_at = now
        else:
            archived_at = None

        connection.execute(
            """
            UPDATE work_items
            SET
                title = ?,
                body_md = ?,
                status = ?,
                parent_id = ?,
                updated_at = ?,
                archived_at = ?
            WHERE id = ?
            """,
            (
                updated_title,
                updated_body,
                updated_status,
                updated_parent_id,
                _serialize_datetime(now),
                _serialize_datetime(archived_at) if archived_at is not None else None,
                normalized_work_item_id,
            ),
        )
        return _load_work_item_by_id(connection, work_item_id=normalized_work_item_id)


def append_work_update(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    work_item_id: str,
    kind: WorkUpdateKind,
    body_md: str,
    session_id: str | None = None,
) -> WorkUpdate:
    normalized_work_item_id = _validate_work_item_id(
        work_item_id,
        message=f"Unknown work item: {work_item_id}",
    )
    normalized_kind = _WORK_UPDATE_KIND_ADAPTER.validate_python(kind)
    normalized_session_id = (
        _validate_session_id(
            session_id,
            message=f"Invalid session id: {session_id}",
        )
        if session_id is not None
        else None
    )
    now = _utc_now()
    update_id = uuid4().hex
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )

    with _connect(db_path) as connection:
        _require_existing_work_item(connection, work_item_id=normalized_work_item_id)
        connection.execute(
            """
            INSERT INTO work_updates (
                id,
                work_item_id,
                kind,
                body_md,
                session_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                update_id,
                normalized_work_item_id,
                normalized_kind,
                body_md,
                normalized_session_id,
                _serialize_datetime(now),
            ),
        )
        connection.execute(
            """
            UPDATE work_items
            SET updated_at = ?
            WHERE id = ?
            """,
            (_serialize_datetime(now), normalized_work_item_id),
        )
        return _load_work_update_by_id(connection, work_update_id=update_id)


def list_work_updates(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    work_item_id: str,
) -> list[WorkUpdate]:
    normalized_work_item_id = _validate_work_item_id(
        work_item_id,
        message=f"Unknown work item: {work_item_id}",
    )
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        _require_existing_work_item(connection, work_item_id=normalized_work_item_id)
        rows = connection.execute(
            """
            SELECT id, work_item_id, kind, body_md, session_id, created_at
            FROM work_updates
            WHERE work_item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (normalized_work_item_id,),
        ).fetchall()
        return [_row_to_work_update(row) for row in rows]


def link_session_to_work_item(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    work_item_id: str,
    session_id: str,
) -> WorkSessionLink:
    normalized_work_item_id = _validate_work_item_id(
        work_item_id,
        message=f"Unknown work item: {work_item_id}",
    )
    normalized_session_id = _validate_session_id(
        session_id,
        message=f"Invalid session id: {session_id}",
    )
    now = _utc_now()
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        _require_existing_work_item(connection, work_item_id=normalized_work_item_id)
        try:
            connection.execute(
                """
                INSERT INTO work_session_links (work_item_id, session_id, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    normalized_work_item_id,
                    normalized_session_id,
                    _serialize_datetime(now),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise WorkGraphError(
                "Session is already linked to this work item"
            ) from error
        return _load_work_session_link(
            connection,
            work_item_id=normalized_work_item_id,
            session_id=normalized_session_id,
        )


def list_work_session_links(
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    workspace_root: Path | str,
    work_item_id: str,
) -> list[WorkSessionLink]:
    normalized_work_item_id = _validate_work_item_id(
        work_item_id,
        message=f"Unknown work item: {work_item_id}",
    )
    db_path = work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as connection:
        _require_existing_work_item(connection, work_item_id=normalized_work_item_id)
        rows = connection.execute(
            """
            SELECT work_item_id, session_id, created_at
            FROM work_session_links
            WHERE work_item_id = ?
            ORDER BY created_at ASC, session_id ASC
            """,
            (normalized_work_item_id,),
        ).fetchall()
        return [_row_to_work_session_link(row) for row in rows]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    return connection


def _load_work_item_by_id(
    connection: sqlite3.Connection,
    *,
    work_item_id: WorkItemId,
) -> WorkItem:
    row = connection.execute(
        """
        SELECT
            id,
            kind,
            slug,
            title,
            status,
            parent_id,
            body_md,
            created_at,
            updated_at,
            archived_at
        FROM work_items
        WHERE id = ?
        """,
        (work_item_id,),
    ).fetchone()
    if row is None:
        raise WorkItemLookupError(f"Unknown work item: {work_item_id}")
    return _row_to_work_item(row)


def _load_work_update_by_id(
    connection: sqlite3.Connection,
    *,
    work_update_id: str,
) -> WorkUpdate:
    row = connection.execute(
        """
        SELECT id, work_item_id, kind, body_md, session_id, created_at
        FROM work_updates
        WHERE id = ?
        """,
        (work_update_id,),
    ).fetchone()
    if row is None:
        raise WorkGraphError(f"Unknown work update: {work_update_id}")
    return _row_to_work_update(row)


def _load_work_session_link(
    connection: sqlite3.Connection,
    *,
    work_item_id: WorkItemId,
    session_id: SessionId,
) -> WorkSessionLink:
    row = connection.execute(
        """
        SELECT work_item_id, session_id, created_at
        FROM work_session_links
        WHERE work_item_id = ? AND session_id = ?
        """,
        (work_item_id, session_id),
    ).fetchone()
    if row is None:
        raise WorkGraphError(
            f"Unknown work-session link: {work_item_id}/{session_id}"
        )
    return _row_to_work_session_link(row)


def _require_existing_work_item(
    connection: sqlite3.Connection,
    *,
    work_item_id: WorkItemId,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM work_items WHERE id = ?",
        (work_item_id,),
    ).fetchone()
    if row is None:
        raise WorkGraphError(f"Unknown parent work item: {work_item_id}")


def _validate_work_item_id(value: str, *, message: str) -> WorkItemId:
    try:
        return _WORK_ITEM_ID_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise WorkGraphError(message) from error


def _validate_session_id(value: str, *, message: str) -> SessionId:
    try:
        return _SESSION_ID_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise WorkGraphError(message) from error


def _row_to_work_item(row: sqlite3.Row) -> WorkItem:
    return _WORK_ITEM_ADAPTER.validate_python(dict(row))


def _row_to_work_update(row: sqlite3.Row) -> WorkUpdate:
    return _WORK_UPDATE_ADAPTER.validate_python(dict(row))


def _row_to_work_session_link(row: sqlite3.Row) -> WorkSessionLink:
    return _WORK_SESSION_LINK_ADAPTER.validate_python(dict(row))


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DEFAULT_WORKSPACES_ROOT",
    "WorkGraphError",
    "WorkItemLookupError",
    "append_work_update",
    "create_work_item",
    "get_work_item_by_slug",
    "link_session_to_work_item",
    "list_work_items",
    "list_work_session_links",
    "list_work_updates",
    "normalize_work_slug",
    "update_work_item",
    "work_graph_db_path",
    "workspace_work_graph_dir",
]
