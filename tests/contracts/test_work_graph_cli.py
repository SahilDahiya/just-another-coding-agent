from __future__ import annotations

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.__main__ import main
from just_another_coding_agent.work_graph import (
    append_work_update,
    create_work_item,
    get_work_item_by_slug,
    list_work_updates,
)


def test_main_work_subcommand_does_not_resolve_default_model(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setattr(entry, "load_config", lambda: {"default_model": "broken"})

    def fail_resolve_default_model(_config):
        raise AssertionError("resolve_default_model should not run for jaca work")

    monkeypatch.setattr(entry, "resolve_default_model", fail_resolve_default_model)

    exit_code = main(
        [
            "work",
            "list",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
        ]
    )

    assert exit_code == 0
    assert "No work items" in capsys.readouterr().out


def test_main_work_new_and_list(tmp_path, monkeypatch, capsys) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setattr(entry, "load_config", lambda: {})

    exit_code = main(
        [
            "work",
            "new",
            "Auth",
            "Store",
                "Cleanup",
                "--session-id",
                "a" * 32,
                "--workspace-root",
                str(workspace_root),
                "--workspaces-root",
                str(workspaces_root),
            ]
    )

    assert exit_code == 0
    created_output = capsys.readouterr().out
    assert "Created task auth-store-cleanup" in created_output
    created_item = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug="auth-store-cleanup",
    )
    assert created_item.created_session_id == "a" * 32

    exit_code = main(
        [
            "work",
            "list",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
        ]
    )

    assert exit_code == 0
    listed_output = capsys.readouterr().out
    assert "auth-store-cleanup" in listed_output
    assert "[todo]" in listed_output
    assert "Auth Store Cleanup" in listed_output


def test_main_work_show_displays_updates_and_links(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    item = create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
        body_md="Tighten auth storage behavior.",
        created_session_id="c" * 32,
    )
    append_work_update(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
        kind="verification",
        body_md="Ran auth store contract tests.",
        session_id="a" * 32,
    )
    monkeypatch.setattr(entry, "load_config", lambda: {})

    exit_code = main(
        [
            "work",
            "show",
            "auth-store-cleanup",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "slug: auth-store-cleanup" in output
    assert "title: Auth Store Cleanup" in output
    assert f"created_session_id: {'c' * 32}" in output
    assert "Tighten auth storage behavior." in output
    assert "verification" in output
    assert "Ran auth store contract tests." in output


def test_main_work_note_and_status_update_store(tmp_path, monkeypatch, capsys) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    create_work_item(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        kind="task",
        title="Auth Store Cleanup",
    )

    monkeypatch.setattr(entry, "load_config", lambda: {})

    note_exit_code = main(
        [
            "work",
            "note",
            "auth-store-cleanup",
            "Need",
            "to",
            "verify",
            "session",
            "resume",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
            "--session-id",
            "c" * 32,
        ]
    )
    assert note_exit_code == 0
    assert "Added note to auth-store-cleanup" in capsys.readouterr().out

    status_exit_code = main(
        [
            "work",
            "status",
            "auth-store-cleanup",
            "done",
            "--note",
            "Verified with focused tests.",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
            "--session-id",
            "d" * 32,
        ]
    )
    assert status_exit_code == 0
    assert "Updated auth-store-cleanup to done" in capsys.readouterr().out

    item = get_work_item_by_slug(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        slug="auth-store-cleanup",
    )
    assert item.status == "done"

    updates = list_work_updates(
        workspaces_root=workspaces_root,
        workspace_root=workspace_root,
        work_item_id=item.id,
    )
    assert [update.kind for update in updates] == ["note", "completion"]
    assert updates[0].session_id == "c" * 32
    assert updates[1].session_id == "d" * 32


def test_main_work_show_returns_clear_error_for_unknown_slug(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setattr(entry, "load_config", lambda: {})

    exit_code = main(
        [
            "work",
            "show",
            "missing-item",
            "--workspace-root",
            str(workspace_root),
            "--workspaces-root",
            str(workspaces_root),
        ]
    )

    assert exit_code == 2
    assert "Unknown work item slug: missing-item" in capsys.readouterr().err
