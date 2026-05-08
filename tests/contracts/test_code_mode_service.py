from __future__ import annotations

import asyncio

import pytest

from just_another_coding_agent.contracts.code_mode import (
    CodeModeExecRequest,
    CodeModeWaitRequest,
)
from just_another_coding_agent.runtime.code_mode import (
    CodeModeCellNotFoundError,
    CodeModeCellService,
)


async def test_code_mode_cell_service_completes_a_cell() -> None:
    service = CodeModeCellService()

    async def runner(ctx):
        ctx.emit("loaded jobs")
        return '{"rows": 5}'

    result = await service.start_cell(
        CodeModeExecRequest(
            source="await tools.read(path='jobs.jsonl')",
            yield_time_ms=100,
        ),
        runner,
    )

    assert result.state == "completed"
    assert result.error is None
    assert [chunk.channel for chunk in result.output] == ["stdout", "result"]
    assert [chunk.text for chunk in result.output] == ["loaded jobs", '{"rows": 5}']
    assert result.elapsed_ms is not None
    assert result.cell_id not in service.active_cell_ids()


async def test_code_mode_cell_service_yields_then_waits_for_completion() -> None:
    service = CodeModeCellService()
    release = asyncio.Event()

    async def runner(ctx):
        ctx.emit("starting")
        await release.wait()
        ctx.emit("finished")
        return "done"

    initial = await service.start_cell(
        CodeModeExecRequest(source="await slow()", yield_time_ms=1),
        runner,
    )

    assert initial.state == "yielded"
    assert initial.output[0].text == "starting"
    assert initial.cell_id in service.active_cell_ids()

    release.set()
    final = await service.wait_cell(
        CodeModeWaitRequest(cell_id=initial.cell_id, yield_time_ms=100),
    )

    assert final.state == "completed"
    assert [chunk.text for chunk in final.output] == ["starting", "finished", "done"]
    assert initial.cell_id not in service.active_cell_ids()


async def test_code_mode_cell_service_terminates_a_running_cell() -> None:
    service = CodeModeCellService()
    cancelled = asyncio.Event()

    async def runner(ctx):
        ctx.emit("starting")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    initial = await service.start_cell(
        CodeModeExecRequest(source="await slow()", yield_time_ms=1),
        runner,
    )

    result = await service.wait_cell(
        CodeModeWaitRequest(cell_id=initial.cell_id, terminate=True),
    )

    assert result.state == "terminated"
    assert cancelled.is_set()
    assert initial.cell_id not in service.active_cell_ids()


async def test_code_mode_cell_service_fails_on_cell_timeout() -> None:
    service = CodeModeCellService()

    async def runner(ctx):
        ctx.emit("starting")
        await asyncio.sleep(60)

    result = await service.start_cell(
        CodeModeExecRequest(
            source="await slow()",
            yield_time_ms=100,
            timeout_ms=1,
        ),
        runner,
    )

    assert result.state == "failed"
    assert result.error is not None
    assert result.error.error_type == "CodeModeTimeoutError"
    assert result.output[0].text == "starting"
    assert result.cell_id not in service.active_cell_ids()


async def test_code_mode_cell_service_truncates_output() -> None:
    service = CodeModeCellService()

    async def runner(ctx):
        ctx.emit("abcdefghijklmnopqrstuvwxyz")
        return "done"

    result = await service.start_cell(
        CodeModeExecRequest(
            source="emit lots",
            yield_time_ms=100,
            max_output_tokens=1,
        ),
        runner,
    )

    assert result.state == "completed"
    assert result.output_truncated is True
    assert result.output[0].truncated is True
    assert result.output[0].text == "abcd"
    assert [chunk.text for chunk in result.output] == ["abcd"]


async def test_code_mode_cell_service_rejects_unknown_cell() -> None:
    service = CodeModeCellService()

    with pytest.raises(CodeModeCellNotFoundError):
        await service.wait_cell(CodeModeWaitRequest(cell_id="missing"))
