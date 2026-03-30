from just_another_coding_agent.contracts.run_events import (
    FindActivityDetails,
    GrepActivityDetails,
    LsActivityDetails,
    ReadActivityDetails,
    ToolActivity,
)
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.activity import (
    build_started_tool_activity,
    build_succeeded_tool_activity,
)
from just_another_coding_agent.tools._activity import shorten_path

_SAMPLE_ARGS_BY_TOOL: dict[str, dict[str, object]] = {
    "read": {"path": "note.txt", "offset": 1, "limit": 10},
    "write": {"path": "note.txt", "content": "hello"},
    "edit": {"path": "note.txt", "old_text": "hello", "new_text": "world"},
    "shell": {"command": "printf ok", "timeout": 5},
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
    "shell": "shell printf ok",
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

        expected_group_kind = (
            "exploration" if tool_name in {"read", "grep", "ls", "find"} else None
        )
        assert activity == ToolActivity(
            title=_EXPECTED_STARTED_TITLE_BY_TOOL[tool_name],
            group_kind=expected_group_kind,
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
        group_kind="exploration",
    )


def test_short_path_is_workspace_relative_for_exploration_tools() -> None:
    workspace_root = "/home/user/project"

    read_details = ReadActivityDetails(
        path="src/main.py",
        short_path=shorten_path("src/main.py", workspace_root),
        offset=None,
        limit=None,
    )
    assert read_details.short_path == "src/main.py"

    grep_details = GrepActivityDetails(
        pattern="TODO",
        path="/home/user/project/src",
        short_path=shorten_path("/home/user/project/src", workspace_root),
    )
    assert grep_details.short_path == "src"

    ls_details = LsActivityDetails(
        path="/home/user/project/src/lib",
        short_path=shorten_path("/home/user/project/src/lib", workspace_root),
    )
    assert ls_details.short_path == "src/lib"

    find_details = FindActivityDetails(
        pattern="*.py",
        path="/home/user/project",
        short_path=shorten_path("/home/user/project", workspace_root),
    )
    assert find_details.short_path == "."


def test_short_path_falls_back_to_basename_for_outside_paths() -> None:
    workspace_root = "/home/user/project"

    assert shorten_path("/etc/config.toml", workspace_root) == "config.toml"
    assert shorten_path("/tmp/data", workspace_root) == "data"


def test_short_path_is_none_when_path_is_none() -> None:
    assert shorten_path(None, "/home/user/project") is None

    grep_details = GrepActivityDetails(
        pattern="TODO",
        path=None,
        short_path=shorten_path(None, "/home/user/project"),
    )
    assert grep_details.short_path is None

    ls_details = LsActivityDetails(
        path=None,
        short_path=shorten_path(None, "/home/user/project"),
    )
    assert ls_details.short_path is None
