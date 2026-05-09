from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from just_another_coding_agent.contracts.rpc import RpcErrorEnvelope
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    SessionLifecycleEvent,
)

RpcHandler = Callable[[Any, "_RpcContext"], AsyncIterator[str]]


@dataclass(frozen=True)
class _RpcContext:
    model: Any
    workspace_root: Path | str
    sessions_root: Path | str
    emit_rpc_event: (
        Callable[[str, RunEvent | SessionLifecycleEvent], Awaitable[None]] | None
    )


@dataclass(frozen=True)
class _RpcErrorMapping:
    exception: type[BaseException]
    error_type: str


def _rpc_error_handler(
    *mappings: _RpcErrorMapping,
) -> Callable[[RpcHandler], RpcHandler]:
    exception_types = tuple(mapping.exception for mapping in mappings)

    def decorator(handler: RpcHandler) -> RpcHandler:
        async def wrapped(request: Any, ctx: _RpcContext) -> AsyncIterator[str]:
            try:
                async for line in handler(request, ctx):
                    yield line
            except exception_types as error:
                error_type = next(
                    mapping.error_type
                    for mapping in mappings
                    if isinstance(error, mapping.exception)
                )
                yield RpcErrorEnvelope(
                    id=request.id,
                    error_type=error_type,
                    message=str(error),
                ).model_dump_json()

        return wrapped

    return decorator
