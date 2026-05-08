from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.runtime.code_mode import CodeModeToolBridge
from just_another_coding_agent.tools.code_mode import code_mode_exec
from just_another_coding_agent.tools.deps import WorkspaceDeps


@dataclass(frozen=True)
class _ParentContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = "call-exec"
    tool_name: str | None = "exec"


def _hello_command() -> str:
    if detect_default_shell_family() == "powershell":
        return "[Console]::Out.Write('hello')"
    return "printf hello"


def _run_context(deps: WorkspaceDeps) -> RunContext[WorkspaceDeps]:
    return RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
        tool_call_id="call-code-mode",
        tool_name="exec",
    )


async def test_code_mode_bridge_calls_canonical_read_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.read(path="note.txt", offset=2, limit=1)
    finally:
        await deps.read_only_worker.close()

    assert result == "world\n"


async def test_code_mode_bridge_publishes_nested_tool_updates(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    updates: list[tuple[str, str, object | None]] = []

    async def sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        updates.append((tool_call_id, tool_name, payload))

    deps = WorkspaceDeps(
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
        tool_update_sink=sink,
    )
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))
    bridge.bind_cell_id("cell-1")

    try:
        result = await bridge.read(path="note.txt")
    finally:
        await deps.read_only_worker.close()

    assert result == "hello"
    assert len(updates) == 2
    assert updates[0] == (
        "call-exec",
        "exec",
        {
            "summary": "read started",
            "details": {
                "kind": "code_mode",
                "cell_id": "cell-1",
                "nested_tool": "read",
                "nested_status": "started",
                "title": "read note.txt",
                "elapsed_ms": 0,
                "error_type": None,
                "message": None,
            },
        },
    )
    assert updates[1][0:2] == ("call-exec", "exec")
    assert updates[1][2]["summary"] == "read succeeded"  # type: ignore[index]
    assert updates[1][2]["details"]["nested_status"] == "succeeded"  # type: ignore[index]
    assert isinstance(updates[1][2]["details"]["elapsed_ms"], int)  # type: ignore[index]


async def test_code_mode_bridge_publishes_failed_nested_tool_update(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    updates: list[tuple[str, str, object | None]] = []

    async def sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        updates.append((tool_call_id, tool_name, payload))

    deps = WorkspaceDeps(
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
        tool_update_sink=sink,
    )
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))
    bridge.bind_cell_id("cell-1")

    try:
        try:
            await bridge.read(path="missing.txt")
        except Exception as exc:
            assert type(exc).__name__ == "ToolPathError"
    finally:
        await deps.read_only_worker.close()

    assert len(updates) == 2
    assert updates[1][0:2] == ("call-exec", "exec")
    assert updates[1][2]["summary"] == "read failed"  # type: ignore[index]
    details = updates[1][2]["details"]  # type: ignore[index]
    assert details["kind"] == "code_mode"
    assert details["nested_tool"] == "read"
    assert details["nested_status"] == "failed"
    assert details["error_type"] == "ToolPathError"
    assert "missing.txt" in details["message"]


async def test_code_mode_bridge_calls_canonical_grep_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("needle one\n", encoding="utf-8")
    (workspace_root / "beta.txt").write_text("other\nneedle two\n", encoding="utf-8")
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.grep(pattern="needle")
    finally:
        await deps.read_only_worker.close()

    assert result == "alpha.txt:1:needle one\nbeta.txt:2:needle two"


async def test_code_mode_bridge_calls_canonical_ls_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (workspace_root / "subdir").mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.ls(path=".", limit=10)
    finally:
        await deps.read_only_worker.close()

    assert result == "alpha.txt\nsubdir/"


async def test_code_mode_bridge_calls_canonical_find_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (workspace_root / "beta.py").write_text("print('beta')\n", encoding="utf-8")
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.find(pattern="*.py", path=".", limit=10)
    finally:
        await deps.read_only_worker.close()

    assert result == "beta.py"


async def test_code_mode_bridge_calls_canonical_write_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.write(path="created.txt", content="hello\n")
    finally:
        await deps.read_only_worker.close()

    assert result == f"Wrote {workspace_root / 'created.txt'}"
    assert (workspace_root / "created.txt").read_text(encoding="utf-8") == "hello\n"


async def test_code_mode_bridge_calls_canonical_edit_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello\nworld\n", encoding="utf-8")
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    try:
        result = await bridge.edit(
            path="note.txt",
            old_text="hello",
            new_text="goodbye",
        )
    finally:
        await deps.read_only_worker.close()

    assert result == f"Edited {workspace_root / 'note.txt'}"
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == (
        "goodbye\nworld\n"
    )


async def test_code_mode_bridge_calls_canonical_shell_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    result = await bridge.shell(command=_hello_command())

    assert result == {"exit_code": 0, "output": "hello"}


async def test_code_mode_bridge_suppresses_raw_nested_shell_updates(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    updates: list[tuple[str, str, object | None]] = []

    async def sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        updates.append((tool_call_id, tool_name, payload))

    deps = WorkspaceDeps(
        workspace_root=workspace_root,
        shell_family=detect_default_shell_family(),
        tool_update_sink=sink,
    )
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))
    bridge.bind_cell_id("cell-1")

    result = await bridge.shell(command=_hello_command())

    assert result == {"exit_code": 0, "output": "hello"}
    assert [tool_name for _call_id, tool_name, _payload in updates] == [
        "exec",
        "exec",
    ]
    assert updates[0][2]["details"]["nested_status"] == "started"  # type: ignore[index]
    assert updates[1][2]["details"]["nested_status"] == "succeeded"  # type: ignore[index]


async def test_code_mode_exec_context_exposes_bridge_tools(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")

    async def runner(ctx):
        content = await ctx.tools.read(path="note.txt")
        ctx.emit(content)
        return "done"

    deps = WorkspaceDeps.from_workspace_root(
        workspace_root,
        code_mode_runner=runner,
    )

    try:
        result = await code_mode_exec(
            _run_context(deps),
            source="await tools.read(path='note.txt')",
            yield_time_ms=100,
        )
    finally:
        await deps.read_only_worker.close()

    assert [chunk["text"] for chunk in result.return_value["output"]] == [
        "hello",
        "done",
    ]
