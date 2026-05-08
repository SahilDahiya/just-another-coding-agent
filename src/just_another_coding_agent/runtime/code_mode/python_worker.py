from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


class _ReturnResult(BaseException):
    def __init__(self, value: Any) -> None:
        super().__init__("return_result called")
        self.value = value


class _ToolError(RuntimeError):
    pass


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def _read_json_line() -> dict[str, Any]:
    line = await asyncio.to_thread(sys.stdin.readline)
    if line == "":
        raise RuntimeError("Code Mode parent closed the protocol stream")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid parent protocol message: {exc}") from exc
    if not isinstance(message, dict):
        raise RuntimeError("parent protocol message must be a JSON object")
    return message


@dataclass
class _PendingToolCall:
    future: asyncio.Future[Any]


class _RuntimeProtocol:
    def __init__(self) -> None:
        self._pending: dict[str, _PendingToolCall] = {}
        self._listener: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._listener = asyncio.create_task(self._listen())

    async def close(self) -> None:
        if self._listener is None:
            return
        self._listener.cancel()
        try:
            await self._listener
        except asyncio.CancelledError:
            pass

    async def _listen(self) -> None:
        while True:
            message = await _read_json_line()
            message_type = message.get("type")
            call_id = message.get("id")
            if not isinstance(call_id, str):
                raise RuntimeError("parent response is missing string id")
            pending = self._pending.pop(call_id, None)
            if pending is None:
                raise RuntimeError(f"unknown tool response id: {call_id}")
            if message_type == "tool_result":
                pending.future.set_result(message.get("result"))
            elif message_type == "tool_error":
                error_text = message.get("error")
                if not isinstance(error_text, str):
                    error_text = "nested tool failed"
                pending.future.set_exception(_ToolError(error_text))
            else:
                pending.future.set_exception(
                    RuntimeError(f"unknown parent response type: {message_type}")
                )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        call_id = f"tool-{uuid4().hex}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[call_id] = _PendingToolCall(future=future)
        _send(
            {
                "type": "tool_call",
                "id": call_id,
                "name": name,
                "arguments": arguments,
            }
        )
        return await future


class _Tools:
    def __init__(self, protocol: _RuntimeProtocol) -> None:
        self._protocol = protocol

    async def read(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("read", kwargs)

    async def grep(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("grep", kwargs)

    async def ls(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("ls", kwargs)

    async def find(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("find", kwargs)

    async def write(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("write", kwargs)

    async def edit(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("edit", kwargs)

    async def shell(self, **kwargs: Any) -> Any:
        return await self._protocol.call_tool("shell", kwargs)


def _emit(value: Any, *, channel: str = "stdout") -> None:
    if channel not in {"stdout", "stderr"}:
        raise ValueError("emit channel must be 'stdout' or 'stderr'")
    _send({"type": "emit", "channel": channel, "text": _stringify(value)})


def _return_result(value: Any = "") -> None:
    raise _ReturnResult(value)


def _restricted_builtins() -> dict[str, Any]:
    return {
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "ValueError": ValueError,
    }


async def _execute_source(source: str, protocol: _RuntimeProtocol) -> Any:
    indented_source = textwrap.indent(source, "    ")
    compiled_source = (
        "async def __code_mode_main__():\n"
        f"{indented_source if indented_source.strip() else '    pass'}\n"
    )
    globals_dict: dict[str, Any] = {
        "__builtins__": _restricted_builtins(),
        "emit": _emit,
        "json": json,
        "return_result": _return_result,
        "tools": _Tools(protocol),
    }
    locals_dict: dict[str, Any] = {}
    exec(compiled_source, globals_dict, locals_dict)
    main = locals_dict["__code_mode_main__"]
    try:
        return await main()
    except _ReturnResult as result:
        return result.value


async def _main() -> int:
    start_message = await _read_json_line()
    if start_message.get("type") != "start":
        raise RuntimeError("first protocol message must be start")
    source = start_message.get("source")
    if not isinstance(source, str) or source == "":
        raise RuntimeError("start message source must be a non-empty string")

    protocol = _RuntimeProtocol()
    protocol.start()
    try:
        result = await _execute_source(source, protocol)
    finally:
        await protocol.close()
    if result is not None:
        _send({"type": "result", "text": _stringify(result)})
    else:
        _send({"type": "result", "text": None})
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception as exc:
        _send(
            {
                "type": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
