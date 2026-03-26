import pytest
from pydantic import ValidationError

from pi_code_agent.rpc.session_store import session_path_for_id


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
