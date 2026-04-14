from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from just_another_coding_agent._pdeathsig import set_pdeathsig_in_child
from just_another_coding_agent.tools.read_only_worker.protocol import (
    READ_ONLY_WORKER_KIND,
    HelloWorkerRequest,
    HelloWorkerResponse,
    ReadOnlyWorkerErrorResponse,
    ShutdownWorkerRequest,
    WorkerResponse,
    encode_worker_message,
    parse_worker_response_line,
    worker_error_to_exception,
)

# asyncio.StreamReader's default per-line buffer is 64 KB, which is smaller
# than legitimate single-line worker responses can reach (a `read` window or
# a `grep` result with many matches easily exceeds 64 KB on one line and
# would raise LimitOverrunError mid-readline). 16 MB is well above any
# realistic single tool result and well below the OS pipe buffer worst case.
_WORKER_STREAM_BUFFER_LIMIT = 16 * 1024 * 1024


class ReadOnlyWorkerClient:
    def __init__(
        self,
        command: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        worker_kind: str = READ_ONLY_WORKER_KIND,
    ) -> None:
        self._command = list(command)
        self._env = env
        self._worker_kind = worker_kind
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[WorkerResponse]] = {}
        self._fatal_error: Exception | None = None

    async def __aenter__(self) -> ReadOnlyWorkerClient:
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        await self.close()

    async def start(self) -> ReadOnlyWorkerClient:
        if self._process is not None:
            return self

        try:
            # preexec_fn is POSIX-only; on Windows it raises ValueError and
            # parent-death propagation is handled differently (not covered
            # here). On Linux we set PR_SET_PDEATHSIG=SIGTERM so the worker
            # cannot outlive this Python backend under any failure mode.
            spawn_kwargs: dict[str, Any] = dict(
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                limit=_WORKER_STREAM_BUFFER_LIMIT,
            )
            if os.name != "nt":
                spawn_kwargs["preexec_fn"] = set_pdeathsig_in_child
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                **spawn_kwargs,
            )
            self._reader_task = asyncio.create_task(self._reader_loop())
            hello = await self.send(HelloWorkerRequest(request_id=uuid4().hex))
            if not isinstance(hello, HelloWorkerResponse):
                raise RuntimeError(
                    "Read-only worker returned non-hello response during startup: "
                    f"{type(hello).__name__}"
                )
            if hello.worker_kind != self._worker_kind:
                raise RuntimeError(
                    "Read-only worker hello response worker_kind mismatch: "
                    f"expected {self._worker_kind!r}, got {hello.worker_kind!r}"
                )
            if not hello.supported_operations:
                raise RuntimeError(
                    "Read-only worker hello response must advertise operations"
                )
            return self
        except Exception:
            await self._close()
            raise

    async def close(self) -> None:
        await self._close()

    async def send(self, message: Any) -> WorkerResponse:
        if self._fatal_error is not None:
            raise RuntimeError(
                "Read-only worker client is not usable after fatal error: "
                f"{self._fatal_error}"
            ) from self._fatal_error
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Read-only worker client is not started")
        request_id = message.request_id
        if request_id in self._pending:
            raise RuntimeError(
                f"Read-only worker request_id already in flight: {request_id!r}"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[WorkerResponse] = loop.create_future()
        self._pending[request_id] = future

        self._process.stdin.write(f"{encode_worker_message(message)}\n".encode("utf-8"))
        await self._process.stdin.drain()

        response = await future
        if isinstance(response, ReadOnlyWorkerErrorResponse):
            raise worker_error_to_exception(response)
        return response

    async def _send_shutdown(self) -> None:
        if self._process is None or self._process.stdin is None:
            return
        shutdown = ShutdownWorkerRequest(request_id=uuid4().hex)
        self._process.stdin.write(
            f"{encode_worker_message(shutdown)}\n".encode("utf-8")
        )
        await self._process.stdin.drain()
        self._process.stdin.close()

    async def _reader_loop(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                try:
                    response = parse_worker_response_line(line.decode("utf-8"))
                except Exception as error:
                    raise RuntimeError(
                        "Read-only worker emitted an invalid protocol response: "
                        f"{error}"
                    ) from error
                future = self._pending.pop(response.request_id, None)
                if future is None or future.done():
                    raise RuntimeError(
                        "Read-only worker returned a response for an unknown "
                        f"request_id: {response.request_id!r}"
                    )
                future.set_result(response)
        except Exception as error:
            await self._fail_pending(error)
            return

        stderr_output = ""
        if self._process.stderr is not None:
            stderr_output = (
                (await self._process.stderr.read())
                .decode("utf-8", errors="replace")
                .strip()
            )
        return_code = await self._process.wait()
        if self._pending:
            message = (
                "Read-only worker exited while requests were in flight"
                if return_code == 0
                else f"Read-only worker exited with code {return_code}"
            )
            if stderr_output:
                message = f"{message}: {stderr_output}"
            await self._fail_pending(RuntimeError(message))

    async def _close(self) -> None:
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
            try:
                await asyncio.wait_for(asyncio.shield(self._reader_task), timeout=2)
            except TimeoutError:
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task
            self._reader_task = None

        self._process = None

    async def _fail_pending(self, error: Exception) -> None:
        self._fatal_error = error
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)


__all__ = ["ReadOnlyWorkerClient"]
