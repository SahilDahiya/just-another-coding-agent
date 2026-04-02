from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from just_another_coding_agent.contracts.work_graph import (
    WorkItemKind,
    WorkItemStatus,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.work_graph.store import (
    DEFAULT_WORKSPACES_ROOT,
    WorkGraphError,
    WorkItemLookupError,
    append_work_update,
    create_work_item,
    get_work_item_by_slug,
    list_work_items,
    list_work_updates,
    update_work_item,
)

_WORK_ITEM_KINDS: tuple[WorkItemKind, ...] = ("project", "task")
_WORK_ITEM_STATUSES: tuple[WorkItemStatus, ...] = (
    "todo",
    "in_progress",
    "blocked",
    "done",
    "archived",
)


def run_work_mode(*, argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="jaca work",
        description="Manage durable workspace-native work items.",
    )
    subparsers = parser.add_subparsers(dest="work_command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new work item")
    _add_work_common_args(new_parser)
    new_parser.add_argument(
        "--kind",
        choices=_WORK_ITEM_KINDS,
        default="task",
        help="Work item kind (default: task)",
    )
    new_parser.add_argument(
        "--slug",
        default=None,
        help="Optional explicit slug. Defaults to normalized title.",
    )
    new_parser.add_argument(
        "--parent",
        default=None,
        help="Optional parent work item slug in this workspace.",
    )
    new_parser.add_argument(
        "--body",
        default="",
        help="Optional markdown body for the work item.",
    )
    new_parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session id that created this work item",
    )
    new_parser.add_argument("title", nargs="+", help="Work item title")

    list_parser = subparsers.add_parser("list", help="List work items")
    _add_work_common_args(list_parser)
    list_parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived work items",
    )

    show_parser = subparsers.add_parser("show", help="Show one work item")
    _add_work_common_args(show_parser)
    show_parser.add_argument("slug", help="Work item slug")

    note_parser = subparsers.add_parser(
        "note",
        help="Append a durable note to a work item",
    )
    _add_work_common_args(note_parser)
    note_parser.add_argument("slug", help="Work item slug")
    note_parser.add_argument("text", nargs="+", help="Note body")
    note_parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session id that produced this note",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Update work item status",
    )
    _add_work_common_args(status_parser)
    status_parser.add_argument("slug", help="Work item slug")
    status_parser.add_argument("status", choices=_WORK_ITEM_STATUSES)
    status_parser.add_argument(
        "--note",
        default=None,
        help="Optional note to append alongside the status change",
    )
    status_parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session id that produced the status update",
    )

    args = parser.parse_args(list(argv))
    workspace_root = normalize_workspace_root(args.workspace_root)
    workspaces_root = _resolve_workspaces_root(args.workspaces_root)

    try:
        if args.work_command == "new":
            return _run_work_new(
                workspace_root=workspace_root,
                workspaces_root=workspaces_root,
                kind=args.kind,
                title=" ".join(args.title),
                slug=args.slug,
                parent_slug=args.parent,
                body_md=args.body,
                created_session_id=args.session_id,
            )
        if args.work_command == "list":
            return _run_work_list(
                workspace_root=workspace_root,
                workspaces_root=workspaces_root,
                include_archived=args.include_archived,
            )
        if args.work_command == "show":
            return _run_work_show(
                workspace_root=workspace_root,
                workspaces_root=workspaces_root,
                slug=args.slug,
            )
        if args.work_command == "note":
            return _run_work_note(
                workspace_root=workspace_root,
                workspaces_root=workspaces_root,
                slug=args.slug,
                text=" ".join(args.text).strip(),
                session_id=args.session_id,
            )
        if args.work_command == "status":
            return _run_work_status(
                workspace_root=workspace_root,
                workspaces_root=workspaces_root,
                slug=args.slug,
                status=args.status,
                note=args.note,
                session_id=args.session_id,
            )
        raise AssertionError(f"unexpected work command: {args.work_command}")
    except (WorkGraphError, WorkItemLookupError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


def _run_work_new(
    *,
    workspace_root: Path,
    workspaces_root: Path,
    kind: WorkItemKind,
    title: str,
    slug: str | None,
    parent_slug: str | None,
    body_md: str,
    created_session_id: str | None,
) -> int:
    parent_id = None
    if parent_slug is not None:
        parent = get_work_item_by_slug(
            workspaces_root=workspaces_root,
            workspace_root=workspace_root,
            slug=parent_slug,
        )
        parent_id = parent.id

    created = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind=kind,
        title=title,
        slug=slug,
        parent_id=parent_id,
        body_md=body_md,
        created_session_id=created_session_id,
    )
    print(f"Created {created.kind} {created.slug}")
    print(f"id: {created.id}")
    return 0


def _run_work_list(
    *,
    workspace_root: Path,
    workspaces_root: Path,
    include_archived: bool,
) -> int:
    items = list_work_items(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        include_archived=include_archived,
    )
    if not items:
        print(f"No work items in workspace: {workspace_root}")
        return 0

    parent_slugs = {item.id: item.slug for item in items}
    print("Work items:")
    for item in items:
        line = f"- {item.kind} {item.slug} [{item.status}] {item.title}"
        if item.parent_id is not None:
            parent_slug = parent_slugs.get(item.parent_id)
            if parent_slug is not None:
                line += f" (parent: {parent_slug})"
        print(line)
    return 0


def _run_work_show(
    *,
    workspace_root: Path,
    workspaces_root: Path,
    slug: str,
) -> int:
    item = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug=slug,
    )
    all_items = list_work_items(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        include_archived=True,
    )
    parent_slug = None
    if item.parent_id is not None:
        parent_slug = next(
            (
                candidate.slug
                for candidate in all_items
                if candidate.id == item.parent_id
            ),
            None,
        )
    updates = list_work_updates(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
    )
    print(f"slug: {item.slug}")
    print(f"title: {item.title}")
    print(f"kind: {item.kind}")
    print(f"status: {item.status}")
    print(f"id: {item.id}")
    if parent_slug is not None:
        print(f"parent: {parent_slug}")
    if item.created_session_id is not None:
        print(f"created_session_id: {item.created_session_id}")
    print(f"created_at: {item.created_at}")
    print(f"updated_at: {item.updated_at}")
    if item.archived_at is not None:
        print(f"archived_at: {item.archived_at}")
    print("body:")
    print(item.body_md if item.body_md else "(empty)")

    if updates:
        print("updates:")
        for update in updates:
            header = f"- {update.kind} @ {update.created_at}"
            if update.session_id is not None:
                header += f" (session {update.session_id})"
            print(header)
            print(f"  {update.body_md}")

    return 0


def _run_work_note(
    *,
    workspace_root: Path,
    workspaces_root: Path,
    slug: str,
    text: str,
    session_id: str | None,
) -> int:
    item = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug=slug,
    )
    append_work_update(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        kind="note",
        body_md=text,
        session_id=session_id,
    )
    print(f"Added note to {item.slug}")
    return 0


def _run_work_status(
    *,
    workspace_root: Path,
    workspaces_root: Path,
    slug: str,
    status: WorkItemStatus,
    note: str | None,
    session_id: str | None,
) -> int:
    item = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug=slug,
    )
    updated = update_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        status=status,
    )
    if note is not None:
        append_work_update(
            workspaces_root=workspaces_root,
            workspace_root=workspace_root,
            work_item_id=updated.id,
            kind="completion" if status == "done" else "status_change",
            body_md=note,
            session_id=session_id,
        )
    print(f"Updated {updated.slug} to {updated.status}")
    return 0


def _resolve_workspaces_root(raw_workspaces_root: str | None) -> Path:
    if raw_workspaces_root is None:
        default_root = DEFAULT_WORKSPACES_ROOT
        default_root.mkdir(parents=True, exist_ok=True)
        return default_root

    workspaces_root = Path(raw_workspaces_root).expanduser().resolve()
    if workspaces_root.exists() and not workspaces_root.is_dir():
        raise NotADirectoryError(
            f"Workspaces root is not a directory: {workspaces_root}"
        )
    workspaces_root.mkdir(parents=True, exist_ok=True)
    return workspaces_root


def _add_work_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--workspaces-root",
        default=None,
        help="Work graph storage root (default: ~/.jaca/workspaces)",
    )


__all__ = ["run_work_mode"]
