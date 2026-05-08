from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from just_another_coding_agent.runtime.code_mode import CodeModeCellContext
from just_another_coding_agent.tools.code_mode import code_mode_exec, code_mode_wait
from just_another_coding_agent.tools.deps import WorkspaceDeps


def _run_context(deps: WorkspaceDeps) -> RunContext[WorkspaceDeps]:
    return RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
        tool_call_id="call-code-mode",
        tool_name="exec",
    )


async def test_code_mode_exec_tool_uses_injected_runner(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    async def runner(ctx: CodeModeCellContext) -> str:
        ctx.emit("seen")
        return "done"

    deps = WorkspaceDeps.from_workspace_root(
        workspace_root,
        code_mode_runner=runner,
    )

    result = await code_mode_exec(
        _run_context(deps),
        source="await tools.read(path='README.md')",
        yield_time_ms=100,
    )

    assert result.return_value["state"] == "completed"
    assert [chunk["text"] for chunk in result.return_value["output"]] == [
        "seen",
        "done",
    ]
    assert result.metadata["title"] == "exec code cell"
    assert result.metadata["display_label"] == "Code"


async def test_code_mode_exec_tool_fails_without_configured_runner(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    with pytest.raises(RuntimeError, match="Code Mode runner is not configured"):
        await code_mode_exec(
            _run_context(deps),
            source="await tools.read(path='README.md')",
        )


async def test_code_mode_wait_tool_uses_shared_cell_service(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    release = asyncio.Event()

    async def runner(ctx: CodeModeCellContext) -> str:
        ctx.emit("starting")
        await release.wait()
        return "done"

    deps = WorkspaceDeps.from_workspace_root(
        workspace_root,
        code_mode_runner=runner,
    )

    initial = await code_mode_exec(
        _run_context(deps),
        source="await slow()",
        yield_time_ms=1,
    )

    assert initial.return_value["state"] == "yielded"
    release.set()

    final = await code_mode_wait(
        _run_context(deps),
        cell_id=initial.return_value["cell_id"],
        yield_time_ms=100,
    )

    assert final.return_value["state"] == "completed"
    assert [chunk["text"] for chunk in final.return_value["output"]] == [
        "starting",
        "done",
    ]
    assert final.metadata["title"] == "wait code cell"
    assert final.metadata["display_label"] == "Code"
