from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.work_graph import (
    WorkItemKind,
    WorkItemStatus,
    WorkUpdateKind,
)
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError
from just_another_coding_agent.work_graph import (
    append_work_update,
    create_work_item,
    get_work_item_by_slug,
    list_work_items,
    list_work_updates,
    update_work_item,
)


def _format_item_line(*, slug: str, kind: str, status: str, title: str) -> str:
    return f"- {kind} {slug} [{status}] {title}"


def _render_work_item(*, item, parent_slug: str | None, updates) -> str:
    lines = [
        f"slug: {item.slug}",
        f"title: {item.title}",
        f"kind: {item.kind}",
        f"status: {item.status}",
        f"id: {item.id}",
    ]
    if parent_slug is not None:
        lines.append(f"parent: {parent_slug}")
    if item.created_session_id is not None:
        lines.append(f"created_session_id: {item.created_session_id}")
    lines.extend(
        [
            f"created_at: {item.created_at}",
            f"updated_at: {item.updated_at}",
        ]
    )
    if item.archived_at is not None:
        lines.append(f"archived_at: {item.archived_at}")
    lines.append("body:")
    lines.append(item.body_md if item.body_md else "(empty)")

    if updates:
        lines.append("updates:")
        for update in updates:
            header = f"- {update.kind} @ {update.created_at}"
            if update.session_id is not None:
                header += f" (session {update.session_id})"
            lines.append(header)
            lines.append(f"  {update.body_md}")

    return "\n".join(lines)


async def work_list(
    ctx: RunContext[WorkspaceDeps],
    parent_slug: Annotated[str | None, Field(min_length=1)] = None,
    include_archived: bool = False,
) -> str:
    """List durable work items in the current workspace.

    Args:
        parent_slug: Optional parent project slug to scope the listing.
        include_archived: Whether archived items should be included.
    """

    items = list_work_items(
        workspace_root=ctx.deps.workspace_root,
        include_archived=include_archived,
    )
    if parent_slug is not None:
        parent = get_work_item_by_slug(
            workspace_root=ctx.deps.workspace_root,
            slug=parent_slug,
        )
        items = [item for item in items if item.parent_id == parent.id]
    if not items:
        result = "No work items found."
    else:
        result = "\n".join(
            _format_item_line(
                slug=item.slug,
                kind=item.kind,
                status=item.status,
                title=item.title,
            )
            for item in items
        )
    title = "work list"
    if parent_slug is not None:
        title = f"work list {truncate_activity_label(parent_slug)}"
    return make_tool_return(
        return_value=result,
        title=title,
        summary="work items listed",
        details=None,
    )


async def work_read(
    ctx: RunContext[WorkspaceDeps],
    slug: Annotated[str, Field(min_length=1)],
) -> str:
    """Read one durable work item by slug.

    Args:
        slug: Work item slug in the current workspace.
    """

    item = get_work_item_by_slug(
        workspace_root=ctx.deps.workspace_root,
        slug=slug,
    )
    items = list_work_items(
        workspace_root=ctx.deps.workspace_root,
        include_archived=True,
    )
    updates = list_work_updates(
        workspace_root=ctx.deps.workspace_root,
        work_item_id=item.id,
    )
    parent_slug = next(
        (candidate.slug for candidate in items if candidate.id == item.parent_id),
        None,
    )
    result = _render_work_item(
        item=item,
        parent_slug=parent_slug,
        updates=updates,
    )
    return make_tool_return(
        return_value=result,
        title=f"work read {truncate_activity_label(slug)}",
        summary="work item loaded",
        details=None,
    )


async def work_create(
    ctx: RunContext[WorkspaceDeps],
    title: Annotated[str, Field(min_length=1)],
    parent_slug: Annotated[str | None, Field(min_length=1)] = None,
    body_md: str = "",
    kind: WorkItemKind = "task",
) -> str:
    """Create a durable work item in the current workspace.

    Args:
        title: Human-readable work item title.
        parent_slug: Required when creating a task so new tasks stay under an
            explicit project.
        body_md: Optional markdown body.
        kind: Work item kind. Use `task` by default; `project` should be used
            only for top-level workstreams.
    """

    if kind == "task" and parent_slug is None:
        raise ToolOperationalError(
            "work_create task requires parent_slug so new tasks stay inside "
            "an explicit project"
        )
    if kind == "project" and parent_slug is not None:
        raise ToolOperationalError("work_create project must not set parent_slug")

    parent_id = None
    if parent_slug is not None:
        parent = get_work_item_by_slug(
            workspace_root=ctx.deps.workspace_root,
            slug=parent_slug,
        )
        if parent.kind != "project":
            raise ToolOperationalError(
                f"Parent work item must be a project, got {parent.slug} ({parent.kind})"
            )
        parent_id = parent.id

    created = create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind=kind,
        title=title,
        parent_id=parent_id,
        body_md=body_md,
        created_session_id=ctx.deps.session_id,
    )
    result = f"Created {created.kind} {created.slug}"
    if parent_slug is not None:
        result += f" under {parent_slug}"
    return make_tool_return(
        return_value=result,
        title=f"work create {truncate_activity_label(title)}",
        summary="work item created",
        details=None,
    )


async def work_update(
    ctx: RunContext[WorkspaceDeps],
    slug: Annotated[str, Field(min_length=1)],
    kind: WorkUpdateKind,
    body_md: str,
) -> str:
    """Append a durable update to a work item.

    Args:
        slug: Work item slug in the current workspace.
        kind: Update kind such as `note`, `verification`, or `completion`.
        body_md: Markdown body for the update.
    """

    item = get_work_item_by_slug(
        workspace_root=ctx.deps.workspace_root,
        slug=slug,
    )
    append_work_update(
        workspace_root=ctx.deps.workspace_root,
        work_item_id=item.id,
        kind=kind,
        body_md=body_md,
        session_id=ctx.deps.session_id,
    )
    return make_tool_return(
        return_value=f"Added {kind} update to {item.slug}",
        title=f"work update {truncate_activity_label(slug)}",
        summary="work item updated",
        details=None,
    )


async def work_status(
    ctx: RunContext[WorkspaceDeps],
    slug: Annotated[str, Field(min_length=1)],
    status: WorkItemStatus,
    note: str | None = None,
) -> str:
    """Update durable work-item status.

    Args:
        slug: Work item slug in the current workspace.
        status: New work-item status.
        note: Optional durable note explaining the status change.
    """

    item = get_work_item_by_slug(
        workspace_root=ctx.deps.workspace_root,
        slug=slug,
    )
    updated = update_work_item(
        workspace_root=ctx.deps.workspace_root,
        work_item_id=item.id,
        status=status,
    )
    if note is not None:
        append_work_update(
            workspace_root=ctx.deps.workspace_root,
            work_item_id=updated.id,
            kind="completion" if status == "done" else "status_change",
            body_md=note,
            session_id=ctx.deps.session_id,
        )
    return make_tool_return(
        return_value=f"Updated {updated.slug} to {updated.status}",
        title=f"work status {truncate_activity_label(slug)}",
        summary="work status updated",
        details=None,
    )


WORK_LIST_TOOL = Tool(
    work_list,
    takes_ctx=True,
    name="work_list",
    description=(
        "List durable workspace work items. Can scope to one parent project by slug "
        "and optionally include archived items."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

WORK_READ_TOOL = Tool(
    work_read,
    takes_ctx=True,
    name="work_read",
    description=(
        "Read one durable work item by slug, including current state, creation "
        "session provenance, and appended updates."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

WORK_CREATE_TOOL = Tool(
    work_create,
    takes_ctx=True,
    name="work_create",
    description=(
        "Create a durable work item. New tasks must be created under an explicit "
        "parent project slug."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

WORK_UPDATE_TOOL = Tool(
    work_update,
    takes_ctx=True,
    name="work_update",
    description=(
        "Append a durable note, decision, verification, status_change, or "
        "completion update to a work item."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

WORK_STATUS_TOOL = Tool(
    work_status,
    takes_ctx=True,
    name="work_status",
    description=(
        "Update a work item's durable status. Can also append an explanatory note "
        "for the status change."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = [
    "WORK_CREATE_TOOL",
    "WORK_LIST_TOOL",
    "WORK_READ_TOOL",
    "WORK_STATUS_TOOL",
    "WORK_UPDATE_TOOL",
    "work_create",
    "work_list",
    "work_read",
    "work_status",
    "work_update",
]
