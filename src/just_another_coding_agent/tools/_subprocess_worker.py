from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
    ToolOperationalError,
    ToolPathError,
)

_KNOWN_ERROR_TYPES: dict[str, type[Exception]] = {
    "ToolCommandError": ToolCommandError,
    "ToolEncodingError": ToolEncodingError,
    "ToolOperationalError": ToolOperationalError,
    "ToolPathError": ToolPathError,
}


def _resolve_operation(name: str) -> Callable[..., Any] | None:
    if name == "find":
        from just_another_coding_agent.tools.find import execute_find

        return execute_find
    if name == "grep":
        from just_another_coding_agent.tools.grep import execute_grep

        return execute_grep
    if name == "ls":
        from just_another_coding_agent.tools.ls import execute_ls

        return execute_ls
    if name == "read":
        from just_another_coding_agent.tools.read import execute_read

        return execute_read
    return None


async def run_blocking_tool_in_subprocess(
    *,
    operation: str,
    kwargs: dict[str, Any],
) -> Any:
    payload = json.dumps(kwargs, sort_keys=True)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "just_another_coding_agent.tools._subprocess_worker",
        operation,
        payload,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_output = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "Blocking tool subprocess failed: "
            f"{error_output or f'exit code {process.returncode}'}"
        )
    response = json.loads(stdout.decode("utf-8"))
    if response.get("ok") is not True:
        error_type = response.get("error_type")
        message = response.get("message", "")
        error_cls = _KNOWN_ERROR_TYPES.get(str(error_type))
        if error_cls is None:
            raise RuntimeError(
                "Blocking tool subprocess returned unknown error type: "
                f"{error_type!r}: {message}"
            )
        raise error_cls(str(message))
    return response["result"]


def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "RuntimeError",
                    "message": "expected operation name and JSON payload",
                }
            )
        )
        return 0
    operation = argv[1]
    kwargs = json.loads(argv[2])
    function = _resolve_operation(operation)
    if function is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "RuntimeError",
                    "message": f"unknown blocking tool operation: {operation}",
                }
            )
        )
        return 0
    try:
        result = function(**kwargs)
    except Exception as error:  # pragma: no cover - exercised via parent process
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
        )
        return 0
    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(_main(sys.argv))
