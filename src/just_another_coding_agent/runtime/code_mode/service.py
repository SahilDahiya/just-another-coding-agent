from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeAlias
from uuid import uuid4

from just_another_coding_agent.contracts.code_mode import (
    CodeModeCellResult,
    CodeModeCellState,
    CodeModeError,
    CodeModeExecRequest,
    CodeModeOutputChannel,
    CodeModeOutputChunk,
    CodeModeWaitRequest,
    is_code_mode_terminal_state,
)
from just_another_coding_agent.contracts.compaction import (
    COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
)

CodeModeRunner: TypeAlias = Callable[
    ["CodeModeCellContext", CodeModeExecRequest],
    Awaitable[str | None],
]


class CodeModeCellNotFoundError(KeyError):
    """Raised when a wait request references an unknown Code Mode cell."""


class CodeModeCellStateError(RuntimeError):
    """Raised when the service would perform an invalid cell transition."""


@dataclass
class _CellOutput:
    chunks: list[CodeModeOutputChunk] = field(default_factory=list)
    truncated: bool = False


@dataclass
class _CellRecord:
    cell_id: str
    task: asyncio.Task[None] | None
    started_at: float
    max_output_tokens: int | None
    output: _CellOutput = field(default_factory=_CellOutput)
    state: CodeModeCellState = "running"
    error: CodeModeError | None = None


class CodeModeCellContext:
    def __init__(
        self,
        service: "CodeModeCellService",
        cell_id: str,
        *,
        tools: Any = None,
    ) -> None:
        self._service = service
        self.cell_id = cell_id
        self.tools = tools

    def emit(
        self,
        text: str,
        *,
        channel: CodeModeOutputChannel = "stdout",
    ) -> None:
        self._service._append_output(
            cell_id=self.cell_id,
            channel=channel,
            text=text,
        )


class CodeModeCellService:
    def __init__(self) -> None:
        self._cells: dict[str, _CellRecord] = {}

    def active_cell_ids(self) -> tuple[str, ...]:
        return tuple(self._cells)

    async def start_cell(
        self,
        request: CodeModeExecRequest,
        runner: CodeModeRunner,
        *,
        tools: Any = None,
    ) -> CodeModeCellResult:
        cell_id = f"cell-{uuid4().hex}"
        record = _CellRecord(
            cell_id=cell_id,
            task=None,
            started_at=time.monotonic(),
            max_output_tokens=request.max_output_tokens,
        )
        self._cells[cell_id] = record
        bind_cell_id = getattr(tools, "bind_cell_id", None)
        if bind_cell_id is not None:
            bind_cell_id(cell_id)
        record.task = asyncio.create_task(
            self._run_cell(
                cell_id=cell_id,
                runner=runner,
                request=request,
                timeout_ms=request.timeout_ms,
                tools=tools,
            )
        )
        return await self._wait_for_cell(record, yield_time_ms=request.yield_time_ms)

    async def wait_cell(self, request: CodeModeWaitRequest) -> CodeModeCellResult:
        record = self._get_cell(request.cell_id)
        if request.terminate and not is_code_mode_terminal_state(record.state):
            task = self._require_task(record)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if is_code_mode_terminal_state(record.state):
                return self._build_result(record, remove_terminal=True)
            self._set_terminal_state(record, "terminated")
            return self._build_result(record, remove_terminal=True)
        return await self._wait_for_cell(record, yield_time_ms=request.yield_time_ms)

    async def _run_cell(
        self,
        *,
        cell_id: str,
        runner: CodeModeRunner,
        request: CodeModeExecRequest,
        timeout_ms: int | None,
        tools: Any,
    ) -> None:
        record = self._get_cell(cell_id)
        context = CodeModeCellContext(self, cell_id, tools=tools)
        try:
            result_text = await asyncio.wait_for(
                runner(context, request),
                timeout=None if timeout_ms is None else timeout_ms / 1000,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._set_terminal_state(
                record,
                "failed",
                error=CodeModeError(
                    error_type="CodeModeTimeoutError",
                    message="Code Mode cell timed out.",
                ),
            )
            return
        except Exception as exc:
            self._set_terminal_state(
                record,
                "failed",
                error=CodeModeError(
                    error_type=type(exc).__name__,
                    message=str(exc),
                ),
            )
            return
        if result_text is not None:
            self._append_output(
                cell_id=cell_id,
                channel="result",
                text=result_text,
            )
        self._set_terminal_state(record, "completed")

    async def _wait_for_cell(
        self,
        record: _CellRecord,
        *,
        yield_time_ms: int | None,
    ) -> CodeModeCellResult:
        if not is_code_mode_terminal_state(record.state):
            task = self._require_task(record)
            timeout_seconds = 0 if yield_time_ms is None else yield_time_ms / 1000
            if timeout_seconds > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=timeout_seconds,
                    )
            elif task.done():
                await task

        remove_terminal = is_code_mode_terminal_state(record.state)
        if remove_terminal:
            return self._build_result(record, remove_terminal=True)
        if record.state == "running":
            record.state = "yielded"
        return self._build_result(record, remove_terminal=False)

    def _append_output(
        self,
        *,
        cell_id: str,
        channel: CodeModeOutputChannel,
        text: str,
    ) -> None:
        record = self._get_cell(cell_id)
        if record.output.truncated:
            return
        chunk = self._bounded_chunk(
            record=record,
            channel=channel,
            text=text,
        )
        if chunk is not None:
            record.output.chunks.append(chunk)

    def _bounded_chunk(
        self,
        *,
        record: _CellRecord,
        channel: CodeModeOutputChannel,
        text: str,
    ) -> CodeModeOutputChunk | None:
        if record.max_output_tokens is None:
            return CodeModeOutputChunk(channel=channel, text=text)

        max_chars = max(
            1,
            record.max_output_tokens * COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
        )
        used_chars = sum(len(chunk.text) for chunk in record.output.chunks)
        remaining_chars = max_chars - used_chars
        if remaining_chars <= 0:
            record.output.truncated = True
            return None
        if len(text) <= remaining_chars:
            return CodeModeOutputChunk(channel=channel, text=text)
        record.output.truncated = True
        return CodeModeOutputChunk(
            channel=channel,
            text=text[:remaining_chars],
            truncated=True,
        )

    def _get_cell(self, cell_id: str) -> _CellRecord:
        try:
            return self._cells[cell_id]
        except KeyError as exc:
            raise CodeModeCellNotFoundError(cell_id) from exc

    def _require_task(self, record: _CellRecord) -> asyncio.Task[None]:
        if record.task is None:
            raise CodeModeCellStateError(
                f"Code Mode cell {record.cell_id} has no running task"
            )
        return record.task

    def _set_terminal_state(
        self,
        record: _CellRecord,
        state: CodeModeCellState,
        *,
        error: CodeModeError | None = None,
    ) -> None:
        if not is_code_mode_terminal_state(state):
            raise CodeModeCellStateError(f"not a terminal Code Mode state: {state}")
        if is_code_mode_terminal_state(record.state):
            raise CodeModeCellStateError(
                f"Code Mode cell {record.cell_id} already ended as {record.state}"
            )
        record.state = state
        record.error = error

    def _build_result(
        self,
        record: _CellRecord,
        *,
        remove_terminal: bool,
    ) -> CodeModeCellResult:
        result = CodeModeCellResult(
            cell_id=record.cell_id,
            state=record.state,
            output=tuple(record.output.chunks),
            error=record.error,
            elapsed_ms=round((time.monotonic() - record.started_at) * 1000),
            output_truncated=record.output.truncated,
        )
        if remove_terminal:
            self._cells.pop(record.cell_id, None)
        return result


__all__ = [
    "CodeModeCellContext",
    "CodeModeCellNotFoundError",
    "CodeModeCellService",
    "CodeModeCellStateError",
    "CodeModeRunner",
]
