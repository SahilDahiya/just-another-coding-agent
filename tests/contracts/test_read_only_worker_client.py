from __future__ import annotations

import sys
from pathlib import Path

import pytest

from just_another_coding_agent.tools.errors import ToolPathError
from just_another_coding_agent.tools.read_only_worker.client import (
    ReadOnlyWorkerClient,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    LsWorkerRequest,
    ReadCallResult,
    ReadWorkerRequest,
)


def _write_worker_script(tmp_path: Path, body: str) -> Path:
    script_path = tmp_path / "fake_worker.py"
    script_path.write_text(body, encoding="utf-8")
    return script_path


async def test_read_only_worker_client_round_trips_read_requests(
    tmp_path: Path,
) -> None:
    script_path = _write_worker_script(
        tmp_path,
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }), flush=True)
    elif request["type"] == "call_read":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "read_result",
            "window_text": "two\\n",
            "total_lines": 3,
            "start_line": 2,
            "end_line": 2,
            "truncated": False,
            "next_offset": 3,
            "first_line_exceeds_max_bytes": False,
        }), flush=True)
    elif request["type"] == "shutdown":
        break
""",
    )

    async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]) as client:
        response = await client.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root="/workspace",
                path="note.txt",
                offset=2,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )

    assert isinstance(response, ReadCallResult)
    assert response.window_text == "two\n"
    assert response.next_offset == 3


async def test_read_only_worker_client_maps_error_responses_to_python_exceptions(
    tmp_path: Path,
) -> None:
    script_path = _write_worker_script(
        tmp_path,
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }), flush=True)
    elif request["type"] == "call_ls":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "error",
            "error_code": "path_error",
            "message": "missing directory",
        }), flush=True)
    elif request["type"] == "shutdown":
        break
""",
    )

    async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]) as client:
        with pytest.raises(ToolPathError, match="missing directory"):
            await client.send(
                LsWorkerRequest(
                    request_id="ls-1",
                    workspace_root="/workspace",
                    path="src",
                    limit=500,
                    max_bytes=50 * 1024,
                )
            )


async def test_read_only_worker_client_rejects_invalid_hello_response(
    tmp_path: Path,
) -> None:
    script_path = _write_worker_script(
        tmp_path,
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "wrong_worker",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }), flush=True)
        break
""",
    )

    with pytest.raises(RuntimeError, match="worker_kind"):
        async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]):
            pass


async def test_read_only_worker_client_fails_pending_requests_on_process_exit(
    tmp_path: Path,
) -> None:
    script_path = _write_worker_script(
        tmp_path,
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }), flush=True)
    elif request["type"] == "call_read":
        sys.exit(0)
""",
    )

    async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]) as client:
        with pytest.raises(RuntimeError, match="exited"):
            await client.send(
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
