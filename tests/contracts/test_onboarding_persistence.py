from __future__ import annotations

import json
import sqlite3

from just_another_coding_agent.onboarding import (
    PublishedMcqQuestion,
    onboarding_db_path,
    publish_onboarding_mcq,
)


def test_publish_onboarding_mcq_abandons_existing_pending_attempt(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "docs").mkdir()
    (workspace_root / "docs" / "goal.md").write_text(
        "Python owns semantics.\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = "0123456789abcdef0123456789abcdef"

    stale_attempt = publish_onboarding_mcq(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
        run_id="run-stale",
        question=PublishedMcqQuestion(
            question_type="mcq",
            prompt="Old question?",
            options=("A", "B", "C", "D"),
            correct_index=1,
            evidence=("docs/goal.md",),
            explanation="Old explanation.",
        ),
    )

    fresh_attempt = publish_onboarding_mcq(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
        run_id="run-fresh",
        question=PublishedMcqQuestion(
            question_type="mcq",
            prompt="Fresh question?",
            options=("W", "X", "Y", "Z"),
            correct_index=2,
            evidence=("docs/goal.md",),
            explanation="Fresh explanation.",
        ),
    )

    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, status, prompt, question_payload_json
            FROM onboarding_attempts
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()

    assert len(rows) == 2
    assert rows[0]["id"] == stale_attempt.attempt_id
    assert rows[0]["status"] == "abandoned"
    assert rows[1]["id"] == fresh_attempt.attempt_id
    assert rows[1]["status"] == "pending"
    assert rows[1]["prompt"] == "Fresh question?"
    assert json.loads(rows[1]["question_payload_json"]) == {
        "correct_index": 2,
        "evidence": ["docs/goal.md"],
        "options": ["W", "X", "Y", "Z"],
    }
