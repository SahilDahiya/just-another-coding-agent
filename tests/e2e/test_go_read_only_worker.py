from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest

from just_another_coding_agent.tools.errors import ToolEncodingError
from just_another_coding_agent.tools.read_only_worker.client import (
    ReadOnlyWorkerClient,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    READ_ONLY_WORKER_OPERATIONS,
    GrepCallResult,
    GrepWorkerRequest,
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
from tests.read_only_worker_test_support import (
    default_read_only_worker_filesystem_policy,
    ensure_built_read_only_worker,
)


def _ripgrep_is_runnable() -> bool:
    executable = shutil.which("rg")
    if executable is None:
        return False
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


class _GoWorkerProcess:
    def __init__(self, *, worker_path: Path) -> None:
        self._worker_path = worker_path
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> _GoWorkerProcess:
        self._process = subprocess.Popen(
            [str(self._worker_path)],
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
        timeout = 30.0 if isinstance(message, HelloWorkerRequest) else 10.0
        return self.send_raw(encode_worker_message(message), timeout=timeout)

    def send_raw(self, payload: str, *, timeout: float = 10.0) -> object:
        assert self._process is not None
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        self._process.stdin.write(f"{payload}\n")
        self._process.stdin.flush()

        queue: Queue[tuple[bool, str | BaseException]] = Queue(maxsize=1)

        def _readline() -> None:
            try:
                queue.put((True, self._process.stdout.readline()))
            except BaseException as error:
                queue.put((False, error))

        Thread(target=_readline, daemon=True).start()
        try:
            ok, value = queue.get(timeout=timeout)
        except Empty as error:
            raise AssertionError(
                f"Go read-only worker did not respond within {timeout:.1f}s"
            ) from error

        if not ok:
            assert isinstance(value, BaseException)
            raise value

        assert isinstance(value, str)
        line = value
        if line:
            return parse_worker_response_line(line)

        stderr_output = ""
        if self._process.stderr is not None:
            stderr_output = self._process.stderr.read()
        raise AssertionError(
            f"Go read-only worker exited without a response:\n{stderr_output.strip()}"
        )


def test_go_read_only_worker_handles_handshake_read_and_ls(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (workspace_root / "src").mkdir()

    with _GoWorkerProcess(worker_path=ensure_built_read_only_worker()) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-1"))
        assert isinstance(hello, HelloWorkerResponse)
        assert hello.supported_operations == READ_ONLY_WORKER_OPERATIONS

        read_result = worker.send(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root=str(workspace_root),
                filesystem_policy=default_read_only_worker_filesystem_policy(),
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
                filesystem_policy=default_read_only_worker_filesystem_policy(),
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


def test_go_read_only_worker_rejects_symlink_escape_from_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    secret_path = outside_root / "secret.txt"
    secret_path.write_text("outside\n", encoding="utf-8")
    try:
        (workspace_root / "secret-link.txt").symlink_to(secret_path)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable in this environment: {error}")

    with _GoWorkerProcess(worker_path=ensure_built_read_only_worker()) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-symlink"))
        assert isinstance(hello, HelloWorkerResponse)

        error_response = worker.send(
            ReadWorkerRequest(
                request_id="read-symlink",
                workspace_root=str(workspace_root),
                filesystem_policy=default_read_only_worker_filesystem_policy(),
                path="secret-link.txt",
                offset=None,
                limit=None,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )

        assert error_response.error_code == "path_error"
        assert "path escapes allowed roots" in error_response.message


def test_go_read_only_worker_grep_returns_after_limit_hit(tmp_path: Path) -> None:
    if not _ripgrep_is_runnable():
        pytest.skip("a runnable ripgrep (rg) is required for grep worker tests")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for index in range(700):
        (workspace_root / f"match_{index:02d}.py").write_text(
            "def target():\n    return 'compaction marker "
            + ("x" * 160)
            + "'\n",
            encoding="utf-8",
        )
    with _GoWorkerProcess(worker_path=ensure_built_read_only_worker()) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-limit"))
        assert isinstance(hello, HelloWorkerResponse)

        grep_result = worker.send_raw(
            encode_worker_message(
                GrepWorkerRequest(
                    request_id="grep-limit",
                    workspace_root=str(workspace_root),
                    filesystem_policy=default_read_only_worker_filesystem_policy(),
                    pattern="compaction",
                    path=".",
                    glob="**/*.py",
                    ignore_case=True,
                    limit=5,
                    max_bytes=50 * 1024,
                    max_line_chars=300,
                )
            ),
            timeout=3.0,
        )
        assert isinstance(grep_result, GrepCallResult)
        assert grep_result.limit_hit is True
        assert grep_result.byte_limit_hit is False
        assert len(grep_result.matches) == 5


def test_go_read_only_worker_grep_returns_after_byte_limit_hit(
    tmp_path: Path,
) -> None:
    if not _ripgrep_is_runnable():
        pytest.skip("a runnable ripgrep (rg) is required for grep worker tests")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for index in range(700):
        (workspace_root / f"wide_{index:02d}.py").write_text(
            "needle = '" + ("x" * 120) + "'\n",
            encoding="utf-8",
        )
    with _GoWorkerProcess(worker_path=ensure_built_read_only_worker()) as worker:
        hello = worker.send(HelloWorkerRequest(request_id="hello-byte-limit"))
        assert isinstance(hello, HelloWorkerResponse)

        grep_result = worker.send_raw(
            encode_worker_message(
                GrepWorkerRequest(
                    request_id="grep-byte-limit",
                    workspace_root=str(workspace_root),
                    filesystem_policy=default_read_only_worker_filesystem_policy(),
                    pattern="needle",
                    path=".",
                    glob="**/*.py",
                    ignore_case=False,
                    limit=50,
                    max_bytes=10,
                    max_line_chars=300,
                )
            ),
            timeout=3.0,
        )
        assert isinstance(grep_result, GrepCallResult)
        assert grep_result.limit_hit is False
        assert grep_result.byte_limit_hit is True
        assert grep_result.matches == []


async def test_go_read_only_worker_rejects_binary_read_input(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "weights.pt").write_bytes(b"\x80\n" * 40000)

    async with ReadOnlyWorkerClient(
        [str(ensure_built_read_only_worker())],
    ) as client:
        with pytest.raises(ToolEncodingError, match="not valid UTF-8 text"):
            await client.send(
                ReadWorkerRequest(
                    request_id="read-binary-1",
                    workspace_root=str(workspace_root),
                    filesystem_policy=default_read_only_worker_filesystem_policy(),
                    path="weights.pt",
                    max_lines=2000,
                    max_bytes=50 * 1024,
                )
            )
