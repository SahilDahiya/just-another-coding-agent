from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.rpc.session_store import (
    SessionLookupError,
    create_fork,
    list_workspace_sessions,
    resolve_session_reference,
    session_path_for_id,
    workspace_sessions_dir,
)
from just_another_coding_agent.session import (
    SessionNameValidationError,
    append_session_name_to_session,
    initialize_session,
    read_session_metadata,
)


@pytest.mark.parametrize(
    "session_id",
    [
        "short",
        "0" * 31,
        "0" * 33,
        "g" * 32,
        "../" + ("0" * 29),
    ],
)
def test_session_path_for_id_fails_on_invalid_session_id(
    tmp_path,
    session_id: str,
) -> None:
    with pytest.raises(ValidationError):
        session_path_for_id(
            sessions_root=tmp_path,
            workspace_root=tmp_path / "workspace",
            session_id=session_id,
        )


def test_session_path_for_id_uses_workspace_shard(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    session_path = session_path_for_id(
        sessions_root=tmp_path / "sessions",
        workspace_root=workspace_root,
        session_id="1" * 32,
    )

    assert session_path.parent == workspace_sessions_dir(
        sessions_root=tmp_path / "sessions",
        workspace_root=workspace_root,
    )


def test_resolve_session_reference_matches_normalized_name_in_workspace(
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    other_workspace_root = tmp_path / "other-workspace"
    workspace_root.mkdir()
    other_workspace_root.mkdir()

    matching_id = "1" * 32
    matching_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=matching_id,
    )
    initialize_session(path=matching_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=matching_path,
        workspace_root=workspace_root,
        name="Auth Store Cleanup",
    )

    other_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=other_workspace_root,
        session_id="2" * 32,
    )
    initialize_session(path=other_path, workspace_root=other_workspace_root)
    append_session_name_to_session(
        path=other_path,
        workspace_root=other_workspace_root,
        name="Auth Store Cleanup",
    )

    resolved = resolve_session_reference(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_ref="Auth Store Cleanup",
    )

    assert resolved.session_id == matching_id
    assert resolved.name == "auth-store-cleanup"


def test_append_session_name_to_session_fails_on_duplicate_name_in_workspace(
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    first_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="1" * 32,
    )
    initialize_session(path=first_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=first_path,
        workspace_root=workspace_root,
        name="Auth Store Cleanup",
    )

    second_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="2" * 32,
    )
    initialize_session(path=second_path, workspace_root=workspace_root)

    with pytest.raises(
        SessionNameValidationError,
        match="Session name already in use in this workspace",
    ):
        append_session_name_to_session(
            path=second_path,
            workspace_root=workspace_root,
            name="Auth Store Cleanup",
        )


def test_list_workspace_sessions_orders_by_metadata_update_time_and_filters_workspace(
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    other_workspace_root = tmp_path / "other-workspace"
    workspace_root.mkdir()
    other_workspace_root.mkdir()

    older_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="1" * 32,
    )
    initialize_session(path=older_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=older_path,
        workspace_root=workspace_root,
        name="old session",
    )

    newer_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="2" * 32,
    )
    initialize_session(path=newer_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=newer_path,
        workspace_root=workspace_root,
        name="new session",
    )

    other_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=other_workspace_root,
        session_id="3" * 32,
    )
    initialize_session(path=other_path, workspace_root=other_workspace_root)
    append_session_name_to_session(
        path=other_path,
        workspace_root=other_workspace_root,
        name="other session",
    )

    older_metadata_path = older_path.with_suffix(".meta.json")
    older_metadata = read_session_metadata(path=older_metadata_path).model_copy(
        update={"updated_at": datetime(2026, 4, 1, 1, 0, tzinfo=UTC)}
    )
    older_metadata_path.write_text(
        older_metadata.model_dump_json(),
        encoding="utf-8",
    )
    newer_metadata_path = newer_path.with_suffix(".meta.json")
    newer_metadata = read_session_metadata(path=newer_metadata_path).model_copy(
        update={"updated_at": datetime(2026, 4, 1, 2, 0, tzinfo=UTC)}
    )
    newer_metadata_path.write_text(
        newer_metadata.model_dump_json(),
        encoding="utf-8",
    )

    listed = list_workspace_sessions(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )

    assert [session.session_id for session in listed] == ["2" * 32, "1" * 32]
    assert [session.name for session in listed] == ["new-session", "old-session"]


def test_create_fork_creates_new_session_with_parent_lineage_and_optional_name(
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    source_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="1" * 32,
    )
    initialize_session(path=source_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=source_path,
        workspace_root=workspace_root,
        name="source session",
    )

    forked = create_fork(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        source_session_id="1" * 32,
        name="forked session",
    )

    assert forked.session_id != "1" * 32
    assert forked.name == "forked-session"
    assert forked.forked_from_session_id == "1" * 32
    fork_metadata = read_session_metadata(
        path=session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=forked.session_id,
        ).with_suffix(".meta.json")
    )
    assert fork_metadata.forked_from_session_id == "1" * 32


def test_resolve_session_reference_exposes_fork_parent_metadata(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    source_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="1" * 32,
    )
    initialize_session(path=source_path, workspace_root=workspace_root)
    create_fork(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        source_session_id="1" * 32,
        name="forked session",
    )

    resolved = resolve_session_reference(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_ref="forked-session",
    )

    assert resolved.forked_from_session_id == "1" * 32


def test_create_fork_rejects_duplicate_name_without_creating_session(tmp_path) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    source_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="1" * 32,
    )
    existing_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id="2" * 32,
    )
    initialize_session(path=source_path, workspace_root=workspace_root)
    initialize_session(path=existing_path, workspace_root=workspace_root)
    append_session_name_to_session(
        path=existing_path,
        workspace_root=workspace_root,
        name="forked session",
    )

    with pytest.raises(
        SessionLookupError,
        match="Session name already in use in this workspace: forked-session",
    ):
        create_fork(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            source_session_id="1" * 32,
            name="forked session",
        )

    listed = list_workspace_sessions(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    assert [session.session_id for session in listed] == ["2" * 32, "1" * 32]


def test_select_session_to_resume_requires_interactive_stdin(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    sessions_root = tmp_path / "sessions"
    workspace_root.mkdir()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(
        RuntimeError,
        match="requires an interactive terminal",
    ):
        entry._select_session_to_resume(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )


def test_select_session_to_resume_caps_display_to_ten_sessions(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    sessions = [
        entry.ResolvedSessionReference(session_id=str(index) * 32, name=f"s-{index}")
        for index in range(1, 13)
    ]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        entry,
        "list_workspace_sessions",
        lambda **_: [
            type(
                "Listed",
                (),
                {
                    "session_id": session.session_id,
                    "name": session.name,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            )()
            for session in sessions
        ],
    )
    monkeypatch.setattr("builtins.input", lambda _: "")

    resolved = entry._select_session_to_resume(
        sessions_root=tmp_path / "sessions",
        workspace_root=workspace_root,
    )

    output = capsys.readouterr().out
    assert "Showing 10 most recent of 12 sessions." in output
    assert "10. s-10" in output
    assert "11. s-11" not in output
    assert resolved.session_id == "1" * 32


def test_select_session_to_resume_still_prompts_when_only_one_session(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        entry,
        "list_workspace_sessions",
        lambda **_: [
            type(
                "Listed",
                (),
                {
                    "session_id": "1" * 32,
                    "name": "only-session",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            )()
        ],
    )
    monkeypatch.setattr("builtins.input", lambda _: "")

    resolved = entry._select_session_to_resume(
        sessions_root=tmp_path / "sessions",
        workspace_root=workspace_root,
    )

    output = capsys.readouterr().out
    assert "Recent sessions" in output
    assert "1. only-session" in output
    assert resolved.session_id == "1" * 32
