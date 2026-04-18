from __future__ import annotations

import subprocess
import sys
import textwrap
import time
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
from tests.read_only_worker_test_support import (
    default_read_only_worker_filesystem_policy,
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
                filesystem_policy=default_read_only_worker_filesystem_policy(),
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
                    filesystem_policy=default_read_only_worker_filesystem_policy(),
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
                    filesystem_policy=default_read_only_worker_filesystem_policy(),
                    path="note.txt",
                    offset=1,
                    limit=1,
                    max_lines=2000,
                    max_bytes=50 * 1024,
                )
            )


async def test_read_only_worker_client_handles_response_lines_larger_than_64kb(
    tmp_path: Path,
) -> None:
    # Regression: asyncio.StreamReader's default per-line buffer is 64 KB,
    # which is smaller than legitimate single-line worker responses can
    # reach. A `read` window or a `grep` result with many matches easily
    # exceeds 64 KB on one line and would raise LimitOverrunError mid-
    # readline. The client must spawn the subprocess with a larger limit
    # so single-line responses up to ~16 MB pass through cleanly.
    payload_size = 200 * 1024  # 200 KB, comfortably above the 64 KB default
    script_path = _write_worker_script(
        tmp_path,
        f"""
import json
import sys

window_text = "x" * {payload_size}

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }}), flush=True)
    elif request["type"] == "call_read":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "read_result",
            "window_text": window_text,
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
    )

    async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]) as client:
        response = await client.send(
            ReadWorkerRequest(
                request_id="read-large",
                workspace_root="/workspace",
                filesystem_policy=default_read_only_worker_filesystem_policy(),
                path="huge.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=1024 * 1024,
            )
        )

    assert isinstance(response, ReadCallResult)
    assert len(response.window_text) == payload_size


async def test_read_only_worker_client_handles_in_contract_payload_after_json_expansion(
    tmp_path: Path,
) -> None:
    # Regression: a tool call that fully honors our own max_bytes contract
    # can still produce a response line larger than the old 64 KB asyncio
    # reader limit, because JSON encoding inflates strings whose characters
    # need escaping. A `read` window of 50 KB minus framing overhead, filled
    # with double-quote characters, is well under the worker's max_bytes
    # contract as text content but each `"` becomes `\"` in the JSON body,
    # roughly doubling the on-the-wire size to ~100 KB on a single line.
    # This is the realistic in-contract failure mode the original 200 KB
    # synthetic test does not cover.
    payload_size = 50 * 1024 - 128  # in-contract under a 50 KB max_bytes budget
    script_path = _write_worker_script(
        tmp_path,
        f"""
import json
import sys

window_text = '"' * {payload_size}

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "hello":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "hello_ok",
            "protocol_version": 1,
            "worker_kind": "read_only",
            "supported_operations": ["read", "ls"],
            "supports_cancel": True,
            "supports_parallel_calls": True,
        }}), flush=True)
    elif request["type"] == "call_read":
        print(json.dumps({{
            "request_id": request["request_id"],
            "type": "read_result",
            "window_text": window_text,
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
    )

    async with ReadOnlyWorkerClient([sys.executable, "-u", str(script_path)]) as client:
        response = await client.send(
            ReadWorkerRequest(
                request_id="read-quotes",
                workspace_root="/workspace",
                filesystem_policy=default_read_only_worker_filesystem_policy(),
                path="quoted.txt",
                offset=1,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )

    assert isinstance(response, ReadCallResult)
    assert len(response.window_text) == payload_size
    assert response.window_text == '"' * payload_size


def _process_alive(pid: int) -> bool:
    """Return True iff a process with the given PID still exists.

    Uses /proc/<pid> on Linux because os.kill(pid, 0) returns True even
    for zombie processes that have exited but not yet been reaped. The
    lifecycle test needs to distinguish a worker that actually exited
    (its /proc entry is gone) from one that is still running.
    """
    return Path(f"/proc/{pid}").exists()


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="PR_SET_PDEATHSIG is Linux-only",
)
def test_read_only_worker_dies_when_python_parent_dies(tmp_path: Path) -> None:
    # Kernel-enforced parent-death propagation: when a Python process that
    # holds a ReadOnlyWorkerClient dies for any reason (clean exit, crash,
    # SIGKILL, abandonment), the worker subprocess must receive SIGTERM
    # from the kernel via PR_SET_PDEATHSIG and exit promptly. This closes
    # the failure mode where abandoned parents left worker subprocesses
    # running for hours or days.
    fake_worker_script = tmp_path / "fake_worker.py"
    fake_worker_script.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time

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
                    # Hello handshake complete; sit idle forever so the
                    # test's parent-death signal is what kills us, not
                    # natural EOF on stdin.
                    time.sleep(3600)
            """
        ),
        encoding="utf-8",
    )

    intermediate_script = tmp_path / "intermediate.py"
    intermediate_script.write_text(
        textwrap.dedent(
            f"""
            import asyncio
            import os
            import sys

            from just_another_coding_agent.tools.read_only_worker.client import (
                ReadOnlyWorkerClient,
            )

            async def main():
                client = ReadOnlyWorkerClient(
                    [sys.executable, "-u", {str(fake_worker_script)!r}]
                )
                await client.start()
                # Print our PID then the worker's PID so the test can
                # identify which subprocess to watch for death.
                print(os.getpid(), flush=True)
                print(client._process.pid, flush=True)
                # Wait forever; the test will SIGKILL us.
                await asyncio.sleep(3600)

            asyncio.run(main())
            """
        ),
        encoding="utf-8",
    )

    intermediate = subprocess.Popen(
        [sys.executable, "-u", str(intermediate_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert intermediate.stdout is not None
        intermediate_pid_line = intermediate.stdout.readline().decode().strip()
        worker_pid_line = intermediate.stdout.readline().decode().strip()
        if not (intermediate_pid_line and worker_pid_line):
            # Only read stderr when the PIDs are missing, because
            # .read() on a live pipe blocks until EOF and the
            # intermediate stays alive for the happy path.
            intermediate.kill()
            intermediate.wait(timeout=2)
            stderr_tail = (
                intermediate.stderr.read().decode()
                if intermediate.stderr
                else ""
            )
            raise AssertionError(
                "intermediate process did not emit PID lines: "
                f"stderr={stderr_tail}"
            )
        worker_pid = int(worker_pid_line)

        # Sanity check: worker is alive right now.
        assert _process_alive(worker_pid), (
            f"worker pid {worker_pid} should be alive before parent death"
        )

        # SIGKILL the intermediate so there is no chance of a graceful
        # cleanup path running. This is the strongest test of
        # PR_SET_PDEATHSIG: even with no userland cleanup, the kernel
        # must deliver SIGTERM to the worker.
        intermediate.kill()
        intermediate.wait(timeout=5)

        # Poll for worker death. On Linux PDEATHSIG is delivered
        # synchronously when the parent dies, so the worker should
        # exit within a few hundred milliseconds. Allow 3 seconds to
        # absorb scheduler jitter and /proc reaping latency.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not _process_alive(worker_pid):
                return  # Worker died as expected — test passes.
            time.sleep(0.05)

        raise AssertionError(
            f"worker pid {worker_pid} still alive 3s after parent death; "
            "PR_SET_PDEATHSIG is not reaching the worker subprocess"
        )
    finally:
        if intermediate.poll() is None:
            intermediate.kill()
            intermediate.wait(timeout=2)
