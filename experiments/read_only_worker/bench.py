from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from just_another_coding_agent.tools.read_only_worker.protocol import (
    HelloWorkerRequest,
    HelloWorkerResponse,
    LsWorkerRequest,
    ReadOnlyWorkerErrorResponse,
    ReadWorkerRequest,
    ShutdownWorkerRequest,
    WorkerResponse,
    encode_worker_message,
    parse_worker_response_line,
)


async def _run_blocking_tool_in_subprocess(
    *,
    operation: str,
    kwargs: dict[str, Any],
) -> Any:
    payload = json.dumps(kwargs, sort_keys=True)
    worker_script = Path(__file__).with_name("python_subprocess_worker.py")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker_script),
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
        raise RuntimeError(
            "Blocking tool subprocess returned an error: "
            f"{response.get('error_type')!r}: {response.get('message', '')}"
        )
    return response["result"]


def _build_corpus(workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    src = workspace_root / "src"
    src.mkdir()
    nested = src / "nested"
    nested.mkdir()

    large_lines = [
        f"line-{index:05d} abcdefghijklmnopqrstuvwxyz0123456789\n"
        for index in range(12000)
    ]
    (workspace_root / "large.txt").write_text(
        "".join(large_lines),
        encoding="utf-8",
    )

    for index in range(300):
        target = src / f"file_{index:03d}.py"
        target.write_text(
            (
                f"# file {index}\n"
                "def run():\n"
                f"    return 'TODO item {index}'\n"
            ),
            encoding="utf-8",
        )

    for index in range(100):
        target = nested / f"inner_{index:03d}.txt"
        target.write_text(
            f"nested file {index}\nTODO nested {index}\n",
            encoding="utf-8",
        )


class _WorkerClient:
    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[WorkerResponse]] = {}

    async def __aenter__(self) -> _WorkerClient:
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        hello = await self.send(HelloWorkerRequest(request_id="hello-bench"))
        if not isinstance(hello, HelloWorkerResponse):
            raise RuntimeError(f"expected hello response, got {type(hello).__name__}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        try:
            await self._send_shutdown()
        except Exception:
            pass

        if self._process is not None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

    async def _send_shutdown(self) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        shutdown = ShutdownWorkerRequest(request_id="shutdown-bench")
        self._process.stdin.write(
            f"{encode_worker_message(shutdown)}\n".encode("utf-8")
        )
        await self._process.stdin.drain()
        self._process.stdin.close()

    async def _reader_loop(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                return
            response = parse_worker_response_line(line.decode("utf-8"))
            future = self._pending.pop(response.request_id, None)
            if future is not None and not future.done():
                future.set_result(response)

    async def send(self, message: Any) -> WorkerResponse:
        assert self._process is not None
        assert self._process.stdin is not None
        request_id = message.request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[WorkerResponse] = loop.create_future()
        self._pending[request_id] = future
        self._process.stdin.write(f"{encode_worker_message(message)}\n".encode("utf-8"))
        await self._process.stdin.drain()
        response = await future
        if isinstance(response, ReadOnlyWorkerErrorResponse):
            raise RuntimeError(
                f"worker error {response.error_code} for {response.request_id}: "
                f"{response.message}"
            )
        return response


def _timed_sync_iterations(
    *,
    iterations: int,
    runner: Callable[[], Awaitable[Any]],
) -> list[float]:
    async def _run() -> list[float]:
        results: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter()
            await runner()
            results.append((time.perf_counter() - started) * 1000)
        return results

    return asyncio.run(_run())


def _summarize(samples_ms: list[float]) -> dict[str, float]:
    return {
        "avg_ms": round(statistics.mean(samples_ms), 3),
        "p95_ms": round(statistics.quantiles(samples_ms, n=20)[18], 3)
        if len(samples_ms) >= 20
        else round(max(samples_ms), 3),
    }


def _run_python_subprocess_baseline(
    workspace_root: Path,
    *,
    iterations: int,
    concurrency: int,
) -> dict[str, Any]:
    read_samples = _timed_sync_iterations(
        iterations=iterations,
        runner=lambda: _run_blocking_tool_in_subprocess(
            operation="read",
            kwargs={
                "workspace_root": str(workspace_root),
                "path": "large.txt",
                "offset": 1,
                "limit": 400,
            },
        ),
    )
    ls_samples = _timed_sync_iterations(
        iterations=iterations,
        runner=lambda: _run_blocking_tool_in_subprocess(
            operation="ls",
            kwargs={
                "workspace_root": str(workspace_root),
                "path": "src",
                "limit": 500,
            },
        ),
    )

    async def _concurrent_read() -> None:
        await asyncio.gather(
            *[
                _run_blocking_tool_in_subprocess(
                    operation="read",
                    kwargs={
                        "workspace_root": str(workspace_root),
                        "path": "large.txt",
                        "offset": 1 + (index * 20),
                        "limit": 200,
                    },
                )
                for index in range(concurrency)
            ]
        )

    started = time.perf_counter()
    asyncio.run(_concurrent_read())
    concurrent_total_ms = round((time.perf_counter() - started) * 1000, 3)

    return {
        "mode": "python_subprocess",
        "warm_read": _summarize(read_samples),
        "warm_ls": _summarize(ls_samples),
        "concurrent_read_total_ms": concurrent_total_ms,
    }


def _build_go_worker(temp_root: Path) -> tuple[Path, dict[str, Any]]:
    binary_path = temp_root / "jaca-read-only-worker"
    go_cache = temp_root / "gocache"
    go_cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GOCACHE"] = str(go_cache)

    started = time.perf_counter()
    subprocess.run(
        [
            "go",
            "build",
            "-o",
            str(binary_path),
            "./cmd/jaca-read-only-worker",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
    )
    build_ms = round((time.perf_counter() - started) * 1000, 3)
    return binary_path, {
        "build_ms": build_ms,
        "binary_size_bytes": binary_path.stat().st_size,
    }


def _build_rust_worker(temp_root: Path) -> tuple[Path, dict[str, Any]]:
    target_dir = temp_root / "rust-target"
    env = dict(os.environ)
    env["RUSTUP_HOME"] = "/tmp/jaca-rustup-home"
    env["CARGO_HOME"] = "/tmp/jaca-cargo-home"
    env["PATH"] = f"/tmp/jaca-cargo-home/bin:{env['PATH']}"
    env["CARGO_TARGET_DIR"] = str(target_dir)

    started = time.perf_counter()
    subprocess.run(
        [
            "cargo",
            "build",
            "--manifest-path",
            "experiments/read_only_worker/rust_worker/Cargo.toml",
            "--release",
            "--quiet",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
    )
    build_ms = round((time.perf_counter() - started) * 1000, 3)
    binary_path = target_dir / "release" / "jaca_read_only_worker_spike"
    return binary_path, {
        "build_ms": build_ms,
        "binary_size_bytes": binary_path.stat().st_size,
    }


def _run_external_worker_benchmark(
    workspace_root: Path,
    *,
    worker_binary: Path,
    env: dict[str, str] | None = None,
    iterations: int,
    concurrency: int,
    mode: str,
) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        cold_started = time.perf_counter()
        async with _WorkerClient([str(worker_binary)], env=env) as worker:
            cold_handshake_ms = round((time.perf_counter() - cold_started) * 1000, 3)

            first_read_started = time.perf_counter()
            await worker.send(
                ReadWorkerRequest(
                    request_id="read-cold",
                    workspace_root=str(workspace_root),
                    path="large.txt",
                    offset=1,
                    limit=400,
                    max_lines=2000,
                    max_bytes=50 * 1024,
                )
            )
            cold_first_read_ms = round(
                (time.perf_counter() - first_read_started) * 1000,
                3,
            )

            read_samples: list[float] = []
            for index in range(iterations):
                started = time.perf_counter()
                await worker.send(
                    ReadWorkerRequest(
                        request_id=f"read-{index}",
                        workspace_root=str(workspace_root),
                        path="large.txt",
                        offset=1,
                        limit=400,
                        max_lines=2000,
                        max_bytes=50 * 1024,
                    )
                )
                read_samples.append((time.perf_counter() - started) * 1000)

            ls_samples: list[float] = []
            for index in range(iterations):
                started = time.perf_counter()
                await worker.send(
                    LsWorkerRequest(
                        request_id=f"ls-{index}",
                        workspace_root=str(workspace_root),
                        path="src",
                        limit=500,
                        max_bytes=50 * 1024,
                    )
                )
                ls_samples.append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            await asyncio.gather(
                *[
                    worker.send(
                        ReadWorkerRequest(
                            request_id=f"burst-{index}-{uuid4().hex}",
                            workspace_root=str(workspace_root),
                            path="large.txt",
                            offset=1 + (index * 20),
                            limit=200,
                            max_lines=2000,
                            max_bytes=50 * 1024,
                        )
                    )
                    for index in range(concurrency)
                ]
            )
            concurrent_total_ms = round(
                (time.perf_counter() - started) * 1000,
                3,
            )

        return {
            "mode": mode,
            "cold_handshake_ms": cold_handshake_ms,
            "cold_first_read_ms": cold_first_read_ms,
            "warm_read": _summarize(read_samples),
            "warm_ls": _summarize(ls_samples),
            "concurrent_read_total_ms": concurrent_total_ms,
        }

    return asyncio.run(_run())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="jaca-read-only-worker-bench-") as temp_dir:
        temp_root = Path(temp_dir)
        workspace_root = temp_root / "workspace"
        _build_corpus(workspace_root)

        baseline = _run_python_subprocess_baseline(
            workspace_root,
            iterations=args.iterations,
            concurrency=args.concurrency,
        )

        go_binary, go_build = _build_go_worker(temp_root)
        go_results = _run_external_worker_benchmark(
            workspace_root,
            worker_binary=go_binary,
            iterations=args.iterations,
            concurrency=args.concurrency,
            mode="go_worker",
        )
        go_results.update(go_build)

        rust_binary, rust_build = _build_rust_worker(temp_root)
        rust_env = dict(os.environ)
        rust_env["RUSTUP_HOME"] = "/tmp/jaca-rustup-home"
        rust_env["CARGO_HOME"] = "/tmp/jaca-cargo-home"
        rust_env["PATH"] = f"/tmp/jaca-cargo-home/bin:{rust_env['PATH']}"
        rust_results = _run_external_worker_benchmark(
            workspace_root,
            worker_binary=rust_binary,
            env=rust_env,
            iterations=args.iterations,
            concurrency=args.concurrency,
            mode="rust_worker",
        )
        rust_results.update(rust_build)

        results = {
            "iterations": args.iterations,
            "concurrency": args.concurrency,
            "python_subprocess": baseline,
            "go_worker": go_results,
            "rust_worker": rust_results,
        }

        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
            return

        print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
