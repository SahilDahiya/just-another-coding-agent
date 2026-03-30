from __future__ import annotations

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
