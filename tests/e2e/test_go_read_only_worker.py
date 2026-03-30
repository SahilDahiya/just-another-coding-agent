from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from just_another_coding_agent.tools.read_only_worker.protocol import (
    READ_ONLY_WORKER_OPERATIONS,
    HelloWorkerRequest,
    HelloWorkerResponse,
    LsCallResult,
    LsWorkerRequest,
    ReadCallResult,
    ReadWorkerRequest,
    ShutdownWorkerRequest,
    encode_worker_message,
    parse_worker_response_line,
)


class _GoWorkerProcess:
    def __init__(self, repo_root: Path, *, go_cache_dir: Path) -> None:
        self._repo_root = repo_root
        self._go_cache_dir = go_cache_dir
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> _GoWorkerProcess:
        self._go_cache_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["GOCACHE"] = str(self._go_cache_dir)
        self._process = subprocess.Popen(
            ["go", "run", "./experiments/read_only_worker/go_worker"],
            cwd=self._repo_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        try:
            self.send_raw(
                encode_worker_message(
                    ShutdownWorkerRequest(request_id="shutdown-test")
                )
            )
        except Exception:
            pass

        assert self._process is not None
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)

    def send(self, message: object) -> object:
        assert hasattr(message, "model_dump_json")
        return self.send_raw(encode_worker_message(message))

    def send_raw(self, payload: str) -> object:
        assert self._process is not None
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        self._process.stdin.write(f"{payload}\n")
        self._process.stdin.flush()

        line = self._process.stdout.readline()
        if line:
            return parse_worker_response_line(line)

        stderr_output = ""
        if self._process.stderr is not None:
            stderr_output = self._process.stderr.read()
        raise AssertionError(
            "Go read-only worker exited without a response:\n"
            f"{stderr_output.strip()}"
        )


def test_go_read_only_worker_handles_handshake_read_and_ls(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (workspace_root / "src").mkdir()
    go_cache_dir = tmp_path / "gocache"

    with _GoWorkerProcess(repo_root, go_cache_dir=go_cache_dir) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-1"))
        assert isinstance(hello, HelloWorkerResponse)
        assert hello.supported_operations == READ_ONLY_WORKER_OPERATIONS

        read_result = worker.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root=str(workspace_root),
                path="note.txt",
                offset=2,
                limit=1,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
        assert isinstance(read_result, ReadCallResult)
        assert read_result.window_text == "two\n"
        assert read_result.total_lines == 3
        assert read_result.start_line == 2
        assert read_result.end_line == 2
        assert read_result.next_offset == 3

        ls_result = worker.send(
            LsWorkerRequest(
                request_id="ls-1",
                workspace_root=str(workspace_root),
                path=".",
                limit=500,
                max_bytes=50 * 1024,
            )
        )
        assert isinstance(ls_result, LsCallResult)
        rendered = json.loads(ls_result.model_dump_json())
        assert rendered["entries"] == [
            {"name": "note.txt", "is_dir": False},
            {"name": "src", "is_dir": True},
        ]
