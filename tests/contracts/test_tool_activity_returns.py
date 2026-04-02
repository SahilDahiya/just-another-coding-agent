from __future__ import annotations

import shutil
import sys
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ToolReturn

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import edit
from just_another_coding_agent.tools.find import find
from just_another_coding_agent.tools.grep import grep
from just_another_coding_agent.tools.ls import ls
from just_another_coding_agent.tools.read import read
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)
from just_another_coding_agent.tools.shell import shell
from just_another_coding_agent.tools.work_graph import (
    work_create,
    work_list,
    work_read,
    work_status,
    work_update,
)
from just_another_coding_agent.tools.write import write
from just_another_coding_agent.work_graph import (
    create_work_item,
    get_work_item_by_slug,
    list_work_updates,
)


def _write_fake_read_only_worker(tmp_path):
    script_path = tmp_path / "fake_read_only_worker.py"
    script_path.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls", "find", "grep"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }), flush=True)
    elif request["type"] == "call_read":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "read_result",
            "window_text": "world\\n",
            "total_lines": 2,
            "start_line": 2,
            "end_line": 2,
            "truncated": False,
            "next_offset": None,
            "first_line_exceeds_max_bytes": False,
        }), flush=True)
    elif request["type"] == "call_grep":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "grep_result",
            "matches": [
                {
                    "path": "note.txt",
                    "line_number": 2,
                    "text": "TODO",
                    "text_truncated": False,
                }
            ],
            "limit_hit": False,
            "byte_limit_hit": False,
            "truncated_lines": False,
        }), flush=True)
    elif request["type"] == "call_ls":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "ls_result",
            "entries": [{"name": "note.txt", "is_dir": False}],
            "total_entries": 1,
            "limit_hit": False,
            "byte_limit_hit": False,
        }), flush=True)
    elif request["type"] == "call_find":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "find_result",
            "matches": ["note.py"],
            "total_matches": 1,
            "limit_hit": False,
            "byte_limit_hit": False,
        }), flush=True)
    elif request["type"] == "shutdown":
        break
""",
        encoding="utf-8",
    )
    return [sys.executable, "-u", str(script_path)]


def _ctx(tmp_path, *, read_only_worker_command=None, session_id=None):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=detect_default_shell_family(),
            session_id=session_id,
            read_only_worker=ReadOnlyWorkerRuntime(command=read_only_worker_command),
        ),
        tool_call_id="call-1",
        tool_name="tool",
    )


async def test_read_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(
        tmp_path,
        read_only_worker_command=_write_fake_read_only_worker(tmp_path),
    )
    (ctx.deps.workspace_root / "note.txt").write_text(
        "hello\nworld\n",
        encoding="utf-8",
    )

    try:
        result = await read(ctx, "note.txt", offset=2, limit=3)
    finally:
        await ctx.deps.read_only_worker.close()

    assert isinstance(result, ToolReturn)
    assert result.return_value == "world\n"
    assert result.metadata == {
        "title": "read note.txt",
        "summary": "read completed",
        "details": {
            "kind": "read",
            "path": "note.txt",
            "short_path": "note.txt",
            "offset": 2,
            "limit": 3,
        },
    }


async def test_write_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)

    result = await write(ctx, "note.txt", "hello")

    assert isinstance(result, ToolReturn)
    assert result.metadata == {
        "title": "write note.txt",
        "summary": "wrote file",
        "details": {
            "kind": "write",
            "path": "note.txt",
            "bytes_written": 5,
        },
    }


async def test_edit_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    note = ctx.deps.workspace_root / "note.txt"
    note.write_text("hello\nworld\n", encoding="utf-8")

    result = await edit(ctx, "note.txt", "world", "agent")

    assert isinstance(result, ToolReturn)
    assert result.return_value == f"Edited {note}"
    assert result.metadata["title"] == "edit note.txt"
    assert result.metadata["summary"] == "edit applied"
    assert result.metadata["details"] == {
        "kind": "edit",
        "path": "note.txt",
        "diff": (f"--- {note}\n+++ {note}\n@@ -1,2 +1,2 @@\n hello\n-world\n+agent\n"),
        "added_lines": 1,
        "removed_lines": 1,
    }


@pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")
async def test_grep_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(
        tmp_path,
        read_only_worker_command=_write_fake_read_only_worker(tmp_path),
    )
    (ctx.deps.workspace_root / "note.txt").write_text("hello\nTODO\n", encoding="utf-8")

    try:
        result = await grep(ctx, "TODO", path=".", glob="*.txt", limit=5)
    finally:
        await ctx.deps.read_only_worker.close()

    assert isinstance(result, ToolReturn)
    assert result.return_value == "note.txt:2:TODO"
    assert result.metadata == {
        "title": "grep TODO",
        "summary": "search completed",
        "details": {
            "kind": "grep",
            "pattern": "TODO",
            "path": ".",
            "short_path": ".",
            "glob": "*.txt",
            "ignore_case": False,
            "literal": False,
            "limit": 5,
        },
    }


async def test_ls_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(
        tmp_path,
        read_only_worker_command=_write_fake_read_only_worker(tmp_path),
    )
    (ctx.deps.workspace_root / "note.txt").write_text("hello\n", encoding="utf-8")

    try:
        result = await ls(ctx, None, 7)
    finally:
        await ctx.deps.read_only_worker.close()

    assert isinstance(result, ToolReturn)
    assert result.return_value == "note.txt"
    assert result.metadata == {
        "title": "ls .",
        "summary": "listing completed",
        "details": {
            "kind": "ls",
            "path": None,
            "short_path": None,
            "limit": 7,
        },
    }


@pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")
async def test_find_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(
        tmp_path,
        read_only_worker_command=_write_fake_read_only_worker(tmp_path),
    )
    (ctx.deps.workspace_root / "note.py").write_text("print('ok')\n", encoding="utf-8")

    try:
        result = await find(ctx, "*.py", ".", 8)
    finally:
        await ctx.deps.read_only_worker.close()

    assert isinstance(result, ToolReturn)
    assert result.return_value == "note.py"
    assert result.metadata == {
        "title": "find *.py",
        "summary": "find completed",
        "details": {
            "kind": "find",
            "pattern": "*.py",
            "path": ".",
            "short_path": ".",
            "limit": 8,
        },
    }


async def test_shell_returns_tool_owned_activity_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.chdir(tmp_path)
    command = (
        "[Console]::Out.Write('ok')"
        if detect_default_shell_family() == "powershell"
        else "printf ok"
    )

    result = await shell(ctx, command, 9)

    assert isinstance(result, ToolReturn)
    assert result.return_value == {"exit_code": 0, "output": "ok"}
    assert result.metadata == {
        "title": f"shell {command}",
        "summary": "command exited 0",
        "details": {
            "kind": "shell",
            "command_preview": command,
            "shell_family": detect_default_shell_family(),
            "timeout": 9,
            "exit_code": 0,
        },
    }


async def test_work_list_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="project",
        title="Bloat and Rot",
    )

    result = await work_list(ctx, parent_slug=None, include_archived=False)

    assert isinstance(result, ToolReturn)
    assert "bloat-and-rot" in result.return_value
    assert result.metadata == {
        "title": "work list",
        "summary": "work items listed",
    }


async def test_work_create_stamps_created_session_id(tmp_path) -> None:
    ctx = _ctx(tmp_path, session_id="a" * 32)
    create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="project",
        title="Bloat and Rot",
    )

    result = await work_create(
        ctx,
        "Trim Dead Re-exports",
        parent_slug="bloat-and-rot",
    )

    assert isinstance(result, ToolReturn)
    assert (
        result.return_value
        == "Created task trim-dead-re-exports under bloat-and-rot"
    )
    assert result.metadata == {
        "title": "work create Trim Dead Re-exports",
        "summary": "work item created",
    }
    item = get_work_item_by_slug(
        workspace_root=ctx.deps.workspace_root,
        slug="trim-dead-re-exports",
    )
    assert item.created_session_id == "a" * 32


async def test_work_read_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path, session_id="a" * 32)
    project = create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="project",
        title="Bloat and Rot",
    )
    create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="task",
        title="Trim Dead Re-exports",
        parent_id=project.id,
    )

    result = await work_read(ctx, "trim-dead-re-exports")

    assert isinstance(result, ToolReturn)
    assert "slug: trim-dead-re-exports" in result.return_value
    assert result.metadata == {
        "title": "work read trim-dead-re-exports",
        "summary": "work item loaded",
    }


async def test_work_update_and_status_stamp_update_session_ids(tmp_path) -> None:
    ctx = _ctx(tmp_path, session_id="b" * 32)
    project = create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="project",
        title="Bloat and Rot",
    )
    item = create_work_item(
        workspace_root=ctx.deps.workspace_root,
        kind="task",
        title="Trim Dead Re-exports",
        parent_id=project.id,
    )

    update_result = await work_update(
        ctx,
        "trim-dead-re-exports",
        "verification",
        "Verified duplicate helper cleanup.",
    )
    status_result = await work_status(
        ctx,
        "trim-dead-re-exports",
        "done",
        note="Finished cleanup and tests.",
    )

    assert isinstance(update_result, ToolReturn)
    assert update_result.metadata == {
        "title": "work update trim-dead-re-exports",
        "summary": "work item updated",
    }
    assert isinstance(status_result, ToolReturn)
    assert status_result.metadata == {
        "title": "work status trim-dead-re-exports",
        "summary": "work status updated",
    }

    updates = list_work_updates(
        workspace_root=ctx.deps.workspace_root,
        work_item_id=item.id,
    )
    assert [update.kind for update in updates] == ["verification", "completion"]
    assert all(update.session_id == "b" * 32 for update in updates)
