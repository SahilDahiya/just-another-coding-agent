from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from pi_code_agent.contracts.rpc import SessionId
from pi_code_agent.session import initialize_session

_SESSION_ID_ADAPTER = TypeAdapter(SessionId)


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


__all__ = ["create_session", "session_path_for_id"]
