from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import build_started_tool_activity

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


def test_activity_metadata_covers_every_canonical_tool() -> None:
    assert set(_SAMPLE_ARGS_BY_TOOL) == set(CANONICAL_TOOL_NAMES)

    for tool_name in CANONICAL_TOOL_NAMES:
        activity = build_started_tool_activity(
            tool_name=tool_name,
            args=_SAMPLE_ARGS_BY_TOOL[tool_name],
            args_valid=True,
        )

        assert activity.details is not None
        assert activity.details.kind == tool_name
