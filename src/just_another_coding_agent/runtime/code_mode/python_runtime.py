from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from asyncio.subprocess import PIPE, Process
from typing import Any

from just_another_coding_agent.runtime.code_mode.service import (
    CodeModeCellContext,
    CodeModeRunner,
)


class CodeModeSourceRuntimeError(RuntimeError):
    """Raised when the Code Mode source runtime fails."""


class PythonSubprocessCodeModeRuntime:
    def __init__(self) -> None:
        self._process: Process | None = None
        self._lock = asyncio.Lock()

    def create_runner(self, source: str) -> CodeModeRunner:
        async def _runner(context: CodeModeCellContext) -> str | None:
            return await self.run_source(context, source)

        return _runner

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            with contextlib.suppress(CodeModeSourceRuntimeError, BrokenPipeError):
                await _write_message(process, {"type": "shutdown"})
            await _close_stdin(process)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=1)
        if process.returncode is None:
            await _terminate_process(process)

    async def run_source(
        self,
        context: CodeModeCellContext,
        source: str,
    ) -> str | None:
        async with self._lock:
            process = await self._ensure_process()
            try:
                await _write_message(
                    process,
                    {
                        "type": "execute",
                        "id": context.cell_id,
                        "source": source,
                    },
                )
                return await self._run_protocol_loop(context, process)
            except asyncio.CancelledError:
                await self._discard_process(process)
                raise
            except Exception:
                if process.returncode is not None:
                    self._process = None
                raise

    async def _ensure_process(self) -> Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "just_another_coding_agent.runtime.code_mode.python_worker",
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
        )
        await _write_message(process, {"type": "start"})
        self._process = process
        return process

    async def _discard_process(self, process: Process) -> None:
        if self._process is process:
            self._process = None
        await _terminate_process(process)

    async def _run_protocol_loop(
        self,
        context: CodeModeCellContext,
        process: Process,
    ) -> str | None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if line == b"":
                break
            message = _decode_child_message(line)
            message_type = message.get("type")
            if message_type == "emit":
                context.emit(
                    _require_string(message, "text"),
                    channel=_require_channel(message),
                )
            elif message_type == "result":
                if message.get("id") != context.cell_id:
                    raise CodeModeSourceRuntimeError(
                        "runtime result id did not match the active cell"
                    )
                text = message.get("text")
                if text is None:
                    return None
                if not isinstance(text, str):
                    raise CodeModeSourceRuntimeError(
                        "runtime result text must be a string or null"
                    )
                return text
            elif message_type == "tool_call":
                await self._handle_tool_call(context, process, message)
            elif message_type == "error":
                error_id = message.get("id")
                if error_id not in {None, context.cell_id}:
                    raise CodeModeSourceRuntimeError(
                        "runtime error id did not match the active cell"
                    )
                if error_id is None:
                    await self._discard_process(process)
                raise CodeModeSourceRuntimeError(_format_child_error(message))
            else:
                raise CodeModeSourceRuntimeError(
                    f"unknown runtime protocol message type: {message_type}"
                )

        await _require_clean_exit(process)
        self._process = None
        return None

    async def _handle_tool_call(
        self,
        context: CodeModeCellContext,
        process: Process,
        message: dict[str, Any],
    ) -> None:
        call_id = _require_string(message, "id")
        tool_name = _require_string(message, "name")
        arguments = message.get("arguments")
        if not isinstance(arguments, dict):
            await _write_message(
                process,
                {
                    "type": "tool_error",
                    "id": call_id,
                    "error": "tool arguments must be a JSON object",
                },
            )
            return
        tool = getattr(context.tools, tool_name, None)
        if tool is None:
            await _write_message(
                process,
                {
                    "type": "tool_error",
                    "id": call_id,
                    "error": f"tool `{tool_name}` is not enabled",
                },
            )
            return
        try:
            result = await tool(**arguments)
        except Exception as exc:
            await _write_message(
                process,
                {
                    "type": "tool_error",
                    "id": call_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        await _write_message(
            process,
            {
                "type": "tool_result",
                "id": call_id,
                "result": result,
            },
        )


async def _write_message(process: Process, message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise CodeModeSourceRuntimeError("runtime stdin is not available")
    try:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    except TypeError as exc:
        raise CodeModeSourceRuntimeError(
            f"runtime protocol message is not JSON serializable: {exc}"
        ) from exc
    process.stdin.write(payload + b"\n")
    await process.stdin.drain()


async def _close_stdin(process: Process) -> None:
    if process.stdin is None or process.stdin.is_closing():
        return
    process.stdin.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await process.stdin.wait_closed()


def _decode_child_message(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CodeModeSourceRuntimeError(
            f"invalid runtime protocol output: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CodeModeSourceRuntimeError(
            "runtime protocol output must be a JSON object"
        )
    return value


def _require_string(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str):
        raise CodeModeSourceRuntimeError(
            f"runtime protocol field `{key}` must be a string"
        )
    return value


def _require_channel(message: dict[str, Any]) -> str:
    channel = _require_string(message, "channel")
    if channel not in {"stdout", "stderr"}:
        raise CodeModeSourceRuntimeError(
            "runtime emit channel must be stdout or stderr"
        )
    return channel


def _format_child_error(message: dict[str, Any]) -> str:
    error_type = message.get("error_type")
    child_message = message.get("message")
    if isinstance(error_type, str) and isinstance(child_message, str):
        return f"{error_type}: {child_message}"
    if isinstance(child_message, str):
        return child_message
    return "Code Mode source runtime failed"


async def _require_clean_exit(process: Process) -> None:
    await _close_stdin(process)
    stderr = await _read_stderr(process)
    exit_code = await process.wait()
    if exit_code != 0:
        detail = f"runtime exited with code {exit_code}"
        if stderr:
            detail = f"{detail}: {stderr}"
        raise CodeModeSourceRuntimeError(detail)


async def _read_stderr(process: Process) -> str:
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(), timeout=0.5)
    except TimeoutError:
        return ""
    return data.decode("utf-8", errors="replace").strip()


async def _terminate_process(process: Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=1)
        return
    if process.returncode is None:
        process.kill()
        await process.wait()


__all__ = [
    "CodeModeSourceRuntimeError",
    "PythonSubprocessCodeModeRuntime",
]
