from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from just_another_coding_agent.tools.read_only_worker.protocol import (
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

_RUSTUP_HOME = "/tmp/jaca-rustup-home"
_CARGO_HOME = "/tmp/jaca-cargo-home"


class _RustWorkerProcess:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> _RustWorkerProcess:
        cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
        rustup = shutil.which("rustup") or str(
            Path.home() / ".cargo" / "bin" / "rustup"
        )
        if not Path(cargo).exists():
            pytest.skip("cargo is not installed")
        if not Path(rustup).exists():
            pytest.skip("rustup is not installed")

        env = dict(os.environ)
        env["RUSTUP_HOME"] = _RUSTUP_HOME
        env["CARGO_HOME"] = _CARGO_HOME
        cargo_bin_dir = str(Path(cargo).resolve().parent)
        env["PATH"] = f"{cargo_bin_dir}:{_CARGO_HOME}/bin:{env['PATH']}"
        os.makedirs(_RUSTUP_HOME, exist_ok=True)
        os.makedirs(_CARGO_HOME, exist_ok=True)
        subprocess.run(
            [rustup, "default", "stable"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self._process = subprocess.Popen(
            [cargo, "run", "--quiet"],
            cwd=self._repo_root / "experiments/read_only_worker/rust_worker",
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
                encode_worker_message(ShutdownWorkerRequest(request_id="shutdown-test"))
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
            f"Rust read-only worker exited without a response:\n{stderr_output.strip()}"
        )


def test_rust_read_only_worker_handles_handshake_read_and_ls(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (workspace_root / "src").mkdir()

    with _RustWorkerProcess(repo_root) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-1"))
        assert isinstance(hello, HelloWorkerResponse)
        assert hello.supported_operations == ("read", "ls")

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
