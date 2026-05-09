from __future__ import annotations

import ast
import asyncio
import collections
import decimal
import functools
import importlib
import inspect
import itertools
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


class _ReturnResult(BaseException):
    def __init__(self, value: Any) -> None:
        super().__init__("return_result called")
        self.value = value


class _ToolError(RuntimeError):
    pass


_ALLOWED_MODULES: dict[str, Any] = {
    "collections": collections,
    "decimal": decimal,
    "functools": functools,
    "itertools": itertools,
    "json": json,
    "math": math,
    "re": re,
    "statistics": statistics,
}


def _restricted_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    del globals, locals
    if level != 0:
        raise ImportError("relative imports are not available in Code Mode")
    if name not in _ALLOWED_MODULES:
        raise ImportError(f"module `{name}` is not available in Code Mode")
    module = importlib.import_module(name)
    if fromlist:
        return module
    return module


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
        self._control_messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

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
            if message_type in {"execute", "shutdown"}:
                await self._control_messages.put(message)
                continue
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

    async def next_control_message(self) -> dict[str, Any]:
        return await self._control_messages.get()

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


def _normalize_tool_arguments(
    *,
    positional_fields: tuple[str, ...],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if len(args) == 1 and isinstance(args[0], dict):
        if kwargs:
            raise TypeError(
                "cannot combine a positional argument dict with keyword arguments"
            )
        arguments = dict(args[0])
    else:
        if len(args) > len(positional_fields):
            raise TypeError(
                f"expected at most {len(positional_fields)} positional arguments; "
                f"got {len(args)}"
            )
        arguments = dict(kwargs)
        for field, value in zip(positional_fields, args, strict=False):
            if field in arguments:
                raise TypeError(
                    f"got multiple values for tool argument `{field}`"
                )
            arguments[field] = value

    for key in arguments:
        if not isinstance(key, str):
            raise TypeError("tool argument names must be strings")
    return arguments


class _Tools:
    def __init__(self, protocol: _RuntimeProtocol) -> None:
        self._protocol = protocol

    async def read(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "read",
            _normalize_tool_arguments(
                positional_fields=("path", "offset", "limit"),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def grep(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "grep",
            _normalize_tool_arguments(
                positional_fields=(
                    "pattern",
                    "path",
                    "glob",
                    "ignore_case",
                    "literal",
                    "limit",
                ),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def ls(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "ls",
            _normalize_tool_arguments(
                positional_fields=("path", "limit"),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def find(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "find",
            _normalize_tool_arguments(
                positional_fields=("pattern", "path", "limit"),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def write(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "write",
            _normalize_tool_arguments(
                positional_fields=("path", "content"),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def edit(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "edit",
            _normalize_tool_arguments(
                positional_fields=("path", "old_text", "new_text"),
                args=args,
                kwargs=kwargs,
            ),
        )

    async def shell(self, *args: Any, **kwargs: Any) -> Any:
        return await self._protocol.call_tool(
            "shell",
            _normalize_tool_arguments(
                positional_fields=("command", "timeout"),
                args=args,
                kwargs=kwargs,
            ),
        )


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
        "__import__": _restricted_import,
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


async def _execute_source(
    source: str,
    namespace: dict[str, Any],
) -> Any:
    code = compile(
        source,
        "<code-mode-cell>",
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    try:
        result = eval(code, namespace, namespace)
        if inspect.isawaitable(result):
            result = await result
        return result
    except _ReturnResult as result:
        return result.value


async def _main() -> int:
    start_message = await _read_json_line()
    if start_message.get("type") != "start":
        raise RuntimeError("first protocol message must be start")

    protocol = _RuntimeProtocol()
    namespace: dict[str, Any] = {
        "__builtins__": _restricted_builtins(),
        "emit": _emit,
        "json": json,
        "return_result": _return_result,
        "tools": _Tools(protocol),
    }
    protocol.start()
    try:
        while True:
            message = await protocol.next_control_message()
            if message.get("type") == "shutdown":
                return 0
            cell_id = message.get("id")
            if not isinstance(cell_id, str):
                raise RuntimeError("execute message id must be a string")
            source = message.get("source")
            if not isinstance(source, str) or source == "":
                raise RuntimeError("execute message source must be a non-empty string")
            try:
                result = await _execute_source(source, namespace)
            except Exception as exc:
                _send(
                    {
                        "type": "error",
                        "id": cell_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue
            if result is not None:
                _send(
                    {
                        "type": "result",
                        "id": cell_id,
                        "text": _stringify(result),
                    }
                )
            else:
                _send({"type": "result", "id": cell_id, "text": None})
    finally:
        await protocol.close()


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
