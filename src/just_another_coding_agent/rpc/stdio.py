from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TextIO

from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.rpc import (
    RpcErrorEnvelope,
    RpcEventEnvelope,
    RpcRequest,
    RpcResponseEnvelope,
    RunStartRequest,
    SessionCompactRequest,
    SessionCompactResponse,
    SessionCompactSummary,
    SessionCreateRequest,
    SessionCreateResponse,
)
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.rpc.session_store import (
    create_session,
    session_path_for_id,
)
from just_another_coding_agent.runtime.compaction import (
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.session import stream_session_run_events
from just_another_coding_agent.session import (
    SessionFormatError,
)

_RPC_REQUEST_ADAPTER = TypeAdapter(RpcRequest)


async def handle_rpc_json_line(
    *,
    line: str,
    model: Any,
    workspace_root: Path | str,
    sessions_root: Path | str,
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
        request = _RPC_REQUEST_ADAPTER.validate_python(payload)
    except ValidationError:
        yield RpcErrorEnvelope(
            id=request_id,
            error_type="InvalidRequest",
            message="Invalid RPC request",
        ).model_dump_json()
        return

    if isinstance(request, SessionCreateRequest):
        session_id = create_session(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
        yield RpcResponseEnvelope(
            id=request.id,
            response=SessionCreateResponse(session_id=session_id),
        ).model_dump_json()
        return

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        session_id=request.payload.session_id,
    )
    if not session_path.exists():
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="UnknownSession",
            message=f"Unknown session_id: {request.payload.session_id}",
        ).model_dump_json()
        return

    if isinstance(request, SessionCompactRequest):
        try:
            compaction = await summarize_and_append_compaction_to_session(
                model=model,
                path=session_path,
                workspace_root=workspace_root,
            )
        except SessionFormatError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidSession",
                message=str(error),
            ).model_dump_json()
            return
        except Exception as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return

        yield RpcResponseEnvelope(
            id=request.id,
            response=SessionCompactResponse(
                compaction_id=compaction.compaction_id,
                summarized_through_run_id=compaction.summarized_through_run_id,
                summary=SessionCompactSummary(
                    current_objective=compaction.summary.current_objective,
                    established_facts=compaction.summary.established_facts,
                    user_preferences=compaction.summary.user_preferences,
                    important_paths=compaction.summary.important_paths,
                    open_questions=compaction.summary.open_questions,
                    unresolved_work=compaction.summary.unresolved_work,
                ),
            ),
        ).model_dump_json()
        return

    assert isinstance(request, RunStartRequest)
    try:
        async for event in stream_session_run_events(
            model=model,
            workspace_root=workspace_root,
            session_path=session_path,
            prompt=request.payload.prompt,
            tool_names=CANONICAL_TOOL_NAMES,
            thinking=request.payload.thinking,
        ):
            yield RpcEventEnvelope(id=request.id, event=event).model_dump_json()
    except SessionFormatError as error:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidSession",
            message=str(error),
        ).model_dump_json()
        return
    except Exception as error:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InternalError",
            message=str(error),
        ).model_dump_json()
        return


async def serve_rpc_stdio(
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    model: Any,
    workspace_root: Path | str,
    sessions_root: Path | str,
) -> None:
    while True:
        line = await asyncio.to_thread(input_stream.readline)
        if line == "":
            return

        async for response_line in handle_rpc_json_line(
            line=line,
            model=model,
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        ):
            output_stream.write(response_line)
            output_stream.write("\n")
            output_stream.flush()


def _extract_request_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        request_id = payload.get("id")
        if isinstance(request_id, str):
            return request_id

    return None
