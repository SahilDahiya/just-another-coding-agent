from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from pi_code_agent.contracts.rpc import (
    RpcErrorEnvelope,
    RpcEventEnvelope,
    RunStartRequest,
)
from pi_code_agent.contracts.tools import CANONICAL_TOOL_NAMES
from pi_code_agent.runtime.session import stream_session_run_events

_RUN_START_REQUEST_ADAPTER = TypeAdapter(RunStartRequest)


async def handle_rpc_json_line(
    *,
    line: str,
    model: Any,
    workspace_root: Path | str,
    session_path: Path,
) -> AsyncIterator[str]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        yield RpcErrorEnvelope(
            id=None,
            error_type="InvalidJSON",
            message="Invalid JSON request",
        ).model_dump_json()
        return

    request_id = _extract_request_id(payload)

    try:
        request = _RUN_START_REQUEST_ADAPTER.validate_python(payload)
    except ValidationError:
        yield RpcErrorEnvelope(
            id=request_id,
            error_type="InvalidRequest",
            message="Invalid RPC request",
        ).model_dump_json()
        return

    async for event in stream_session_run_events(
        model=model,
        workspace_root=workspace_root,
        session_path=session_path,
        prompt=request.payload.prompt,
        tool_names=CANONICAL_TOOL_NAMES,
    ):
        yield RpcEventEnvelope(id=request.id, event=event).model_dump_json()


def _extract_request_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        request_id = payload.get("id")
        if isinstance(request_id, str):
            return request_id

    return None
