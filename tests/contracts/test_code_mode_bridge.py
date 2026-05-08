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


async def test_code_mode_bridge_calls_canonical_shell_tool(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    bridge = CodeModeToolBridge(_ParentContext(deps=deps))

    result = await bridge.shell(command=_hello_command())

    assert result == {"exit_code": 0, "output": "hello"}


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
