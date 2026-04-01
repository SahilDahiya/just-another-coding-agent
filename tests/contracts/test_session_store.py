import pytest
from pydantic import ValidationError

from just_another_coding_agent.rpc.session_store import (
    SessionLookupError,
    resolve_session_reference,
    session_path_for_id,
)
from just_another_coding_agent.session import (
    append_session_name_to_session,
    initialize_session,
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
            session_id=session_id,
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


def test_resolve_session_reference_fails_on_ambiguous_name_in_same_workspace(
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for session_id in ("1" * 32, "2" * 32):
        path = session_path_for_id(
            sessions_root=sessions_root,
            session_id=session_id,
        )
        initialize_session(path=path, workspace_root=workspace_root)
        append_session_name_to_session(
            path=path,
            workspace_root=workspace_root,
            name="Auth Store Cleanup",
        )

    with pytest.raises(SessionLookupError, match="Ambiguous session name"):
        resolve_session_reference(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_ref="auth-store-cleanup",
        )
