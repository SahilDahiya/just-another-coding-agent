from __future__ import annotations

import shutil
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
from just_another_coding_agent.tools.shell import shell
from just_another_coding_agent.tools.write import write


def _ctx(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return SimpleNamespace(
        deps=WorkspaceDeps.from_workspace_root(workspace_root),
        tool_call_id="call-1",
        tool_name="tool",
    )


async def test_read_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.deps.workspace_root / "note.txt").write_text(
        "hello\nworld\n",
        encoding="utf-8",
    )

    result = await read(ctx, "note.txt", offset=2, limit=3)

    assert isinstance(result, ToolReturn)
    assert result.return_value == "world\n"
    assert result.metadata == {
        "title": "read note.txt",
        "summary": "read completed",
        "details": {
            "kind": "read",
            "path": "note.txt",
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
        "diff": (
            f"--- {note}\n"
            f"+++ {note}\n"
            "@@ -1,2 +1,2 @@\n"
            " hello\n"
            "-world\n"
            "+agent\n"
        ),
        "added_lines": 1,
        "removed_lines": 1,
    }


@pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")
async def test_grep_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.deps.workspace_root / "note.txt").write_text("hello\nTODO\n", encoding="utf-8")

    result = await grep(ctx, "TODO", path=".", glob="*.txt", limit=5)

    assert isinstance(result, ToolReturn)
    assert result.metadata == {
        "title": "grep TODO",
        "summary": "search completed",
        "details": {
            "kind": "grep",
            "pattern": "TODO",
            "path": ".",
            "glob": "*.txt",
            "ignore_case": False,
            "literal": False,
            "limit": 5,
        },
    }


async def test_ls_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.deps.workspace_root / "note.txt").write_text("hello\n", encoding="utf-8")

    result = await ls(ctx, None, 7)

    assert isinstance(result, ToolReturn)
    assert result.metadata == {
        "title": "ls .",
        "summary": "listing completed",
        "details": {
            "kind": "ls",
            "path": None,
            "limit": 7,
        },
    }


@pytest.mark.skipif(shutil.which("rg") is None, reason="rg required")
async def test_find_returns_tool_owned_activity_metadata(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.deps.workspace_root / "note.py").write_text("print('ok')\n", encoding="utf-8")

    result = await find(ctx, "*.py", ".", 8)

    assert isinstance(result, ToolReturn)
    assert result.metadata == {
        "title": "find *.py",
        "summary": "find completed",
        "details": {
            "kind": "find",
            "pattern": "*.py",
            "path": ".",
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
