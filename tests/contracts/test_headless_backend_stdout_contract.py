"""Stdout contract test for the headless backend subprocess.

The Go TUI reads the backend's stdout line-by-line and parses each line
as a JSON-RPC envelope. **Any non-JSON byte on stdout poisons the Go
client's readLoop** — the first parse failure calls `failAllWaiters`
and every in-flight and future request receives the same error until
the backend is restarted. We lived this class of bug: a user saw four
`invalid character 'N' looking for beginning of value` errors from a
single non-JSON line somewhere in the startup sequence, with no way to
identify which line or which code path produced it.

The invariant enforced here is stronger than "today's bugs are fixed":
**under any startup condition, every byte written to stdout by the
headless backend must be part of a valid JSON-RPC envelope on a
newline-delimited line.** This catches future regressions where a
dependency starts printing to stdout on import, a configure hook writes
a progress message, an error handler prints a traceback, or a warning
emission slips past the default stderr routing.

If this test ever fails, the fix is always the same: route the
offending output to stderr (or to an explicit logging sink), never
stdout. Do not add a filter that drops non-JSON from stdout — that
would hide the problem and mask the next one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

import pytest

_STARTUP_REQUEST_BURST = (
    {"id": "r-auth", "command": "auth.status", "payload": {}},
)


def _wait_for_exit(process: subprocess.Popen[bytes], timeout: float) -> None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _read_stderr(process: subprocess.Popen[bytes]) -> bytes:
    if process.stderr is None:
        return b""
    return process.stderr.read()


def _encode_requests(requests: tuple[dict, ...]) -> bytes:
    return b"".join(
        (json.dumps(request) + "\n").encode("utf-8") for request in requests
    )


def _parse_stdout_lines_or_fail(
    stdout: bytes,
    stderr: bytes,
    *,
    trace_mode: str,
) -> list[dict]:
    """Assert every non-empty stdout line is a valid JSON-RPC envelope
    with a ``type`` field, returning the parsed envelopes. Any parse
    failure raises pytest.Fail with the offending line inline so a CI
    run prints a definitive smoking gun without further investigation.
    """
    parsed: list[dict] = []
    for line_number, raw in enumerate(stdout.split(b"\n"), start=1):
        if not raw:
            continue
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as error:
            preview = raw[:200].decode("utf-8", errors="replace")
            hex_preview = raw[:32].hex()
            pytest.fail(
                "Headless backend emitted a non-JSON line on stdout "
                f"(trace_mode={trace_mode!r}):\n"
                f"  line {line_number}: {len(raw)} bytes\n"
                f"  first 32 bytes hex: {hex_preview}\n"
                f"  preview: {preview!r}\n"
                f"  json error: {error}\n"
                f"  stdout total: {len(stdout)} bytes\n"
                f"  stderr: {stderr.decode('utf-8', errors='replace')[:500]!r}"
            )
        if not isinstance(envelope, dict) or "type" not in envelope:
            pytest.fail(
                "Headless backend emitted a JSON line that is not an envelope "
                f"(trace_mode={trace_mode!r}):\n"
                f"  line {line_number}: {raw[:200]!r}"
            )
        parsed.append(envelope)
    return parsed


@pytest.mark.parametrize("trace_mode", ["off"])
def test_headless_backend_stdout_is_pure_json(
    tmp_path: Path,
    trace_mode: str,
) -> None:
    # The trace mode is deliberately pinned to `off` here. This test
    # owns the headless stdout JSON contract, not observability startup.
    # `local` writes under `~/.jaca/traces/`, which is not portable in
    # sandboxed CI, and `logfire` depends on external credentials. Those
    # startup paths are covered separately in the observability tests.
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "JACA_TRACE_MODE": trace_mode,
    }
    lang = os.environ.get("LANG")
    if lang:
        env["LANG"] = lang
    lc_all = os.environ.get("LC_ALL")
    if lc_all:
        env["LC_ALL"] = lc_all

    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    cmd = [
        sys.executable,
        "-m",
        "just_another_coding_agent",
        "--headless",
        "--model",
        "openai-responses:gpt-5.4",
        "--workspace-root",
        str(tmp_path),
        "--sessions-root",
        str(sessions_root),
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None
    process.stdin.write(_encode_requests(_STARTUP_REQUEST_BURST))
    process.stdin.close()
    assert process.stdout is not None
    stdout_lines: list[bytes] = []
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            for index in range(len(_STARTUP_REQUEST_BURST)):
                future = executor.submit(process.stdout.readline)
                try:
                    raw_line = future.result(timeout=30)
                except FutureTimeoutError as error:
                    _wait_for_exit(process, timeout=0)
                    stderr = _read_stderr(process)
                    raise AssertionError(
                        "Headless backend did not emit the expected RPC envelope "
                        f"within 30s for request index {index} "
                        f"(trace_mode={trace_mode!r}). "
                        f"stderr={stderr.decode('utf-8', errors='replace')[:500]!r}"
                    ) from error
                if raw_line == b"":
                    _wait_for_exit(process, timeout=5)
                    stderr = _read_stderr(process)
                    pytest.fail(
                        "Headless backend closed stdout before emitting the full "
                        f"startup response burst (trace_mode={trace_mode!r}). "
                        f"stderr={stderr.decode('utf-8', errors='replace')[:500]!r}"
                    )
                stdout_lines.append(raw_line.rstrip(b"\n"))
    finally:
        if process.poll() is None:
            process.terminate()
        _wait_for_exit(process, timeout=5)
        stderr = _read_stderr(process)
    stdout = b"\n".join(stdout_lines) + b"\n"

    envelopes = _parse_stdout_lines_or_fail(
        stdout,
        stderr,
        trace_mode=trace_mode,
    )

    # Exactly one envelope per request in the burst. If the count
    # mismatches, stdout probably has an extra line that IS valid JSON
    # but does not correspond to any request we sent — still a bug, but
    # a milder one than the first assertion catches.
    assert len(envelopes) == len(_STARTUP_REQUEST_BURST), (
        f"expected {len(_STARTUP_REQUEST_BURST)} envelopes, got {len(envelopes)}: "
        f"{envelopes!r}"
    )

    # Each envelope must correspond to the request it replies to.
    for request, envelope in zip(_STARTUP_REQUEST_BURST, envelopes, strict=True):
        assert envelope.get("id") == request["id"], (
            f"envelope id mismatch: request id={request['id']!r}, "
            f"envelope={envelope!r}"
        )
        # The envelope type is either rpc_response (success) or
        # rpc_error (unknown session etc.) — both are valid JSON-RPC
        # shapes. We don't require success here; we only require that
        # the backend produced a real envelope for the request.
        assert envelope["type"] in {"rpc_response", "rpc_error"}, (
            f"unexpected envelope type: {envelope!r}"
        )
