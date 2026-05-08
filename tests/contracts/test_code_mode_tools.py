from __future__ import annotations

import asyncio

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


async def test_code_mode_exec_tool_uses_default_python_runtime(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text(
        "needle one\nplain line\nneedle two\n",
        encoding="utf-8",
    )
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source=(
            "content = await tools.read(path='README.md')\n"
            "emit('read:' + content.splitlines()[0])\n"
            "emit('json:' + str(json.loads('{\"count\": 2}')['count']))\n"
            "matches = await tools.grep("
            "pattern='needle', path='README.md', literal=True)\n"
            "return_result(matches)"
        ),
        yield_time_ms=1000,
    )
    await deps.read_only_worker.close()

    assert result.return_value["state"] == "completed"
    assert [chunk["text"] for chunk in result.return_value["output"]] == [
        "read:needle one",
        "json:2",
        "README.md:1:needle one\nREADME.md:3:needle two",
    ]


async def test_code_mode_exec_default_runtime_blocks_direct_filesystem_access(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("secret\n", encoding="utf-8")
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source="return_result(open('README.md').read())",
        yield_time_ms=1000,
    )

    assert result.return_value["state"] == "failed"
    assert result.return_value["error"]["error_type"] == "CodeModeSourceRuntimeError"
    assert "name 'open' is not defined" in result.return_value["error"]["message"]


async def test_code_mode_exec_default_runtime_reports_source_exception(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source="raise ValueError('bad source')",
        yield_time_ms=1000,
    )

    assert result.return_value["state"] == "failed"
    assert result.return_value["error"]["error_type"] == "CodeModeSourceRuntimeError"
    assert "ValueError: bad source" in result.return_value["error"]["message"]


async def test_code_mode_exec_default_runtime_reports_unknown_tool(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source="await tools.missing_tool()",
        yield_time_ms=1000,
    )

    assert result.return_value["state"] == "failed"
    assert result.return_value["error"]["error_type"] == "CodeModeSourceRuntimeError"
    assert (
        "'_Tools' object has no attribute 'missing_tool'"
        in result.return_value["error"]["message"]
    )


async def test_code_mode_exec_default_runtime_fails_on_nested_read_error(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source="await tools.read(path='missing.txt')",
        yield_time_ms=1000,
    )
    await deps.read_only_worker.close()

    assert result.return_value["state"] == "failed"
    assert result.return_value["error"]["error_type"] == "CodeModeSourceRuntimeError"
    assert "missing.txt" in result.return_value["error"]["message"]


async def test_code_mode_exec_default_runtime_times_out(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source="while True:\n    pass",
        yield_time_ms=1000,
        timeout_ms=50,
    )

    assert result.return_value["state"] == "failed"
    assert result.return_value["error"]["error_type"] == "CodeModeTimeoutError"
    assert result.return_value["error"]["message"] == "Code Mode cell timed out."


async def test_code_mode_exec_default_runtime_return_result_is_terminal(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deps = WorkspaceDeps.from_workspace_root(workspace_root)

    result = await code_mode_exec(
        _run_context(deps),
        source=(
            "try:\n"
            "    return_result('done')\n"
            "except Exception:\n"
            "    return_result('caught')"
        ),
        yield_time_ms=1000,
    )

    assert result.return_value["state"] == "completed"
    assert [chunk["text"] for chunk in result.return_value["output"]] == ["done"]


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
