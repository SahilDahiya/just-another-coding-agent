from __future__ import annotations

import pytest

from just_another_coding_agent.work_graph import (
    WorkGraphError,
    append_work_update,
    create_work_item,
    get_work_item_by_slug,
    link_session_to_work_item,
    list_work_items,
    list_work_session_links,
    list_work_updates,
    update_work_item,
    work_graph_db_path,
)
from just_another_coding_agent.workspace_storage import workspace_key


def test_work_graph_db_path_uses_workspace_shard(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    path = work_graph_db_path(
        workspaces_root=tmp_path / "workspaces",
        workspace_root=workspace_root,
    )

    assert path.parent.name == workspace_key(workspace_root)
    assert path.name == "work.sqlite"


def test_create_work_item_normalizes_slug_and_reads_back(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    created = create_work_item(
        workspaces_root=tmp_path / "workspaces",
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
        body_md="Tighten auth storage behavior.",
    )

    assert created.slug == "auth-store-cleanup"
    assert created.kind == "task"
    assert created.status == "todo"
    assert created.body_md == "Tighten auth storage behavior."

    loaded = get_work_item_by_slug(
        workspaces_root=tmp_path / "workspaces",
        workspace_root=workspace_root,
        slug="auth-store-cleanup",
    )
    assert loaded == created


def test_create_work_item_rejects_duplicate_slug_in_workspace(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    create_work_item(
        workspaces_root=tmp_path / "workspaces",
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
    )

    with pytest.raises(WorkGraphError, match="already in use"):
        create_work_item(
            workspaces_root=tmp_path / "workspaces",
            workspace_root=workspace_root,
            kind="task",
            title="auth-store-cleanup",
        )


def test_same_slug_is_allowed_in_different_workspaces(tmp_path) -> None:
    first_workspace = tmp_path / "workspace-a"
    second_workspace = tmp_path / "workspace-b"
    first_workspace.mkdir()
    second_workspace.mkdir()
    workspaces_root = tmp_path / "workspaces"

    first = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=first_workspace,
        kind="task",
        title="Bloat and Rot",
    )
    second = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=second_workspace,
        kind="task",
        title="Bloat and Rot",
    )

    assert first.slug == second.slug == "bloat-and-rot"
    assert work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=first_workspace,
    ) != work_graph_db_path(
        workspaces_root=workspaces_root,
        workspace_root=second_workspace,
    )


def test_create_project_and_child_task(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    project = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="project",
        title="Bloat and Rot",
    )
    task = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Trim Dead Re-exports",
        parent_id=project.id,
    )

    assert project.kind == "project"
    assert task.parent_id == project.id

    listed = list_work_items(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
    )
    assert [item.slug for item in listed] == [
        "bloat-and-rot",
        "trim-dead-re-exports",
    ]


def test_create_work_item_rejects_unknown_parent_id(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(WorkGraphError, match="Unknown parent work item"):
        create_work_item(
            workspaces_root=tmp_path / "workspaces",
            workspace_root=workspace_root,
            kind="task",
            title="Trim Dead Re-exports",
            parent_id="missing-parent",
        )


def test_append_work_update_advances_item_updated_at_and_keeps_history(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    item = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
    )
    original_updated_at = item.updated_at

    update = append_work_update(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        kind="verification",
        body_md="Ran auth store contract tests.",
        session_id="a" * 32,
    )

    assert update.work_item_id == item.id
    assert update.kind == "verification"
    assert update.session_id == "a" * 32

    refreshed = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug=item.slug,
    )
    assert refreshed.updated_at > original_updated_at

    updates = list_work_updates(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
    )
    assert len(updates) == 1
    assert updates[0] == update


def test_update_work_item_status_to_archived_sets_archived_at(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    item = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
    )

    archived = update_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        status="archived",
    )

    assert archived.status == "archived"
    assert archived.archived_at is not None


def test_link_session_to_work_item_is_explicit_and_unique(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    item = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
    )

    link = link_session_to_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        session_id="b" * 32,
    )

    assert link.work_item_id == item.id
    assert link.session_id == "b" * 32

    with pytest.raises(WorkGraphError, match="already linked"):
        link_session_to_work_item(
            workspaces_root=workspaces_root,
            workspace_root=workspace_root,
            work_item_id=item.id,
            session_id="b" * 32,
        )

    links = list_work_session_links(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
    )
    assert links == [link]
