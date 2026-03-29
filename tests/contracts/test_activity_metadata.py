from just_another_coding_agent.contracts.run_events import ToolActivity
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import (
    build_started_tool_activity,
    build_succeeded_tool_activity,
)

_SAMPLE_ARGS_BY_TOOL: dict[str, dict[str, object]] = {
    "read": {"path": "note.txt", "offset": 1, "limit": 10},
    "write": {"path": "note.txt", "content": "hello"},
    "edit": {"path": "note.txt", "old_text": "hello", "new_text": "world"},
    "bash": {"command": "printf ok", "timeout": 5},
    "grep": {
        "pattern": "TODO",
        "path": "src",
        "glob": "*.py",
        "ignore_case": False,
        "literal": False,
        "limit": 10,
    },
    "ls": {"path": "src", "limit": 20},
    "find": {"pattern": "*.py", "path": "src", "limit": 20},
}

_EXPECTED_STARTED_TITLE_BY_TOOL = {
    "read": "read note.txt",
    "write": "write note.txt",
    "edit": "edit note.txt",
    "bash": "bash printf ok",
    "grep": "grep TODO",
    "ls": "ls src",
    "find": "find *.py",
}

def test_started_activity_uses_backend_owned_titles_only() -> None:
    assert set(_SAMPLE_ARGS_BY_TOOL) == set(CANONICAL_TOOL_NAMES)
    assert set(_EXPECTED_STARTED_TITLE_BY_TOOL) == set(CANONICAL_TOOL_NAMES)

    for tool_name in CANONICAL_TOOL_NAMES:
        activity = build_started_tool_activity(
            tool_name=tool_name,
            args=_SAMPLE_ARGS_BY_TOOL[tool_name],
            args_valid=True,
        )

        assert activity == ToolActivity(
            title=_EXPECTED_STARTED_TITLE_BY_TOOL[tool_name]
        )


def test_succeeded_activity_prefers_tool_owned_metadata() -> None:
    activity = build_succeeded_tool_activity(
        tool_name="read",
        args={"path": "wrong.txt"},
        args_valid=True,
        result="hello\nworld\n",
        result_metadata={
            "title": "read note.txt",
            "summary": "read completed",
            "details": {
                "kind": "read",
                "path": "note.txt",
                "offset": 2,
                "limit": 5,
            },
        },
        duration_ms=12,
    )

    assert activity == ToolActivity(
        title="read note.txt",
        summary="read completed",
        duration_ms=12,
        details={
            "kind": "read",
            "path": "note.txt",
            "offset": 2,
            "limit": 5,
        },
    )
