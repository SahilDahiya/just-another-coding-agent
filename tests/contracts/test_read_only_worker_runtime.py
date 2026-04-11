from __future__ import annotations

import sys
from pathlib import Path

from just_another_coding_agent.tools.read_only_worker import runtime as runtime_module
from just_another_coding_agent.tools.read_only_worker.protocol import (
    ReadCallResult,
    ReadWorkerRequest,
)
from just_another_coding_agent.tools.read_only_worker.runtime import (
    ReadOnlyWorkerRuntime,
)


def _write_worker_script(tmp_path: Path, counter_path: Path) -> Path:
    script_path = tmp_path / "counting_worker.py"
    script_path.write_text(
        f"""
import json
import pathlib
import sys

counter_path = pathlib.Path({str(counter_path)!r})
counter_path.write_text(
    counter_path.read_text(encoding="utf-8") + "started\\n",
    encoding="utf-8",
)

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls", "find", "grep"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }}), flush=True)
    elif request["type"] == "call_read":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "read_result",
            "window_text": "value\\n",
            "total_lines": 1,
            "start_line": 1,
            "end_line": 1,
            "truncated": False,
            "next_offset": None,
            "first_line_exceeds_max_bytes": False,
        }}), flush=True)
    elif request["type"] == "shutdown":
        break
""",
        encoding="utf-8",
    )
    return script_path


async def test_read_only_worker_runtime_reuses_a_single_worker_process(
    tmp_path: Path,
) -> None:
    counter_path = tmp_path / "counter.txt"
    counter_path.write_text("", encoding="utf-8")
    script_path = _write_worker_script(tmp_path, counter_path)
    runtime = ReadOnlyWorkerRuntime(command=[sys.executable, "-u", str(script_path)])

    try:
        first = await runtime.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root="/workspace",
                path="note.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
        second = await runtime.send(
            ReadWorkerRequest(
                request_id="read-2",
                workspace_root="/workspace",
                path="note.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
    finally:
        await runtime.close()

    assert isinstance(first, ReadCallResult)
    assert isinstance(second, ReadCallResult)
    assert counter_path.read_text(encoding="utf-8").splitlines() == ["started"]


async def test_read_only_worker_runtime_uses_managed_tool_env(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, command, *, env=None) -> None:
            observed["command"] = command
            observed["env"] = env

        async def start(self):
            return self

        async def send(self, message):
            return message

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        runtime_module,
        "ReadOnlyWorkerClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        runtime_module,
        "build_tool_process_env",
        lambda base_env=None: {"PATH": "managed-bin"},
    )

    runtime = ReadOnlyWorkerRuntime(command=["worker"])
    try:
        result = await runtime.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root="/workspace",
                path="note.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
    finally:
        await runtime.close()

    assert isinstance(result, ReadWorkerRequest)
    assert observed["command"] == ["worker"]
    assert observed["env"] == {"PATH": "managed-bin"}


async def test_read_only_worker_runtime_bootstraps_rg_on_windows(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, command, *, env=None) -> None:
            observed["command"] = command
            observed["env"] = env

        async def start(self):
            return self

        async def send(self, message):
            return message

        async def close(self) -> None:
            return None

    monkeypatch.setattr(runtime_module, "ReadOnlyWorkerClient", _FakeClient)
    monkeypatch.setattr(runtime_module.os, "name", "nt")
    monkeypatch.setattr(
        runtime_module,
        "ensure_windows_search_tool",
        lambda tool, *, silent=True: observed.setdefault("bootstrapped", []).append(
            (tool, silent)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "build_tool_process_env",
        lambda base_env=None: {"PATH": "managed-bin"},
    )

    runtime = ReadOnlyWorkerRuntime(command=["worker"])
    try:
        result = await runtime.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root="/workspace",
                path="note.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
    finally:
        await runtime.close()

    assert isinstance(result, ReadWorkerRequest)
    assert observed["bootstrapped"] == [("rg", True)]
    assert observed["env"] == {"PATH": "managed-bin"}
