from __future__ import annotations

import asyncio
from collections.abc import Sequence

from just_another_coding_agent.tools.read_only_worker.client import (
    ReadOnlyWorkerClient,
)
from just_another_coding_agent.tools.read_only_worker.launcher import (
    resolve_read_only_worker_command,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    WorkerRequest,
    WorkerResponse,
)
from just_another_coding_agent.tools.windows_search_tools import (
    build_tool_process_env,
)


class ReadOnlyWorkerRuntime:
    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = tuple(command) if command is not None else None
        self._env = dict(env) if env is not None else None
        self._client: ReadOnlyWorkerClient | None = None
        self._lock = asyncio.Lock()

    async def send(self, message: WorkerRequest) -> WorkerResponse:
        client = await self._ensure_client()
        return await client.send(message)

    async def close(self) -> None:
        async with self._lock:
            if self._client is None:
                return

            client = self._client
            self._client = None
            await client.close()

    async def _ensure_client(self) -> ReadOnlyWorkerClient:
        async with self._lock:
            if self._client is not None:
                return self._client

            command = list(
                self._command
                if self._command is not None
                else resolve_read_only_worker_command()
            )
            client = ReadOnlyWorkerClient(
                command,
                env=(
                    dict(self._env)
                    if self._env is not None
                    else build_tool_process_env()
                ),
            )
            await client.start()
            self._client = client
            return client


__all__ = ["ReadOnlyWorkerRuntime"]
