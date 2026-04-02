from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from .rpc import SessionId

WorkItemId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
WorkUpdateId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
WorkSlug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
WorkItemKind = Literal["project", "task"]
WorkItemStatus = Literal["todo", "in_progress", "blocked", "done", "archived"]
WorkUpdateKind = Literal[
    "note",
    "decision",
    "verification",
    "status_change",
    "completion",
]


class _WorkGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkItem(_WorkGraphModel):
    id: WorkItemId
    kind: WorkItemKind
    slug: WorkSlug
    title: str
    status: WorkItemStatus
    parent_id: WorkItemId | None = None
    body_md: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class WorkUpdate(_WorkGraphModel):
    id: WorkUpdateId
    work_item_id: WorkItemId
    kind: WorkUpdateKind
    body_md: str
    session_id: SessionId | None = None
    created_at: datetime


class WorkSessionLink(_WorkGraphModel):
    work_item_id: WorkItemId
    session_id: SessionId
    created_at: datetime


__all__ = [
    "WorkItem",
    "WorkItemId",
    "WorkItemKind",
    "WorkItemStatus",
    "WorkSessionLink",
    "WorkSlug",
    "WorkUpdate",
    "WorkUpdateId",
    "WorkUpdateKind",
]
