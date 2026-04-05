from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TextIO

from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.auth import (
    AuthStoreError,
    ProviderSecretValidationError,
    clear_provider_secret,
    get_local_secret_store_status,
    list_provider_auth_statuses,
    set_provider_secret,
)
from just_another_coding_agent.contracts.model_catalog import (
    CANONICAL_PROVIDER_ORDER,
    default_model_for_provider,
    shipped_models_for_provider,
)
from just_another_coding_agent.contracts.rpc import (
    AuthClearRequest,
    AuthClearResponse,
    AuthSetRequest,
    AuthSetResponse,
    AuthStatusRequest,
    AuthStatusResponse,
    ModelCatalogModel,
    ModelCatalogProvider,
    ModelCatalogRequest,
    ModelCatalogResponse,
    RpcErrorEnvelope,
    RpcEventEnvelope,
    RpcRequest,
    RpcResponseEnvelope,
    RunEnqueueRequest,
    RunEnqueueResponse,
    RunStartRequest,
    SessionCompactRequest,
    SessionCompactResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionNameRequest,
    SessionNameResponse,
    SessionPreviewRequest,
    SessionPreviewResponse,
)
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.provider_readiness import ProviderReadinessError
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
    SessionNameValidationError,
    append_session_name_to_session,
    build_session_preview,
)

_RPC_REQUEST_ADAPTER = TypeAdapter(RpcRequest)


class _FollowUpState:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_sessions: set[str] = set()
        self._queues: dict[str, deque[str]] = defaultdict(deque)

    async def activate(self, session_id: str) -> None:
        async with self._lock:
            self._active_sessions.add(session_id)

    async def deactivate(self, session_id: str) -> None:
        async with self._lock:
            self._active_sessions.discard(session_id)
            if not self._queues.get(session_id):
                self._queues.pop(session_id, None)

    async def enqueue(self, session_id: str, prompt: str) -> int:
        async with self._lock:
            if session_id not in self._active_sessions:
                raise RuntimeError(
                    "Follow-up queueing requires an active run for this session."
                )
            queue = self._queues[session_id]
            queue.append(prompt)
            return len(queue)

    async def take_next(self, session_id: str) -> str | None:
        async with self._lock:
            queue = self._queues.get(session_id)
            if not queue:
                return None
            prompt = queue.popleft()
            if not queue:
                self._queues.pop(session_id, None)
            return prompt


_FOLLOW_UP_STATE = _FollowUpState()


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

    if isinstance(request, ModelCatalogRequest):
        yield RpcResponseEnvelope(
            id=request.id,
            response=ModelCatalogResponse(
                providers=[
                    ModelCatalogProvider(
                        provider=provider,
                        default_model_id=default_model_for_provider(provider),
                        models=[
                            ModelCatalogModel(
                                model_id=model.model_id,
                                description=model.description,
                            )
                            for model in shipped_models_for_provider(provider)
                        ],
                    )
                    for provider in CANONICAL_PROVIDER_ORDER
                ]
            ),
        ).model_dump_json()
        return

    if isinstance(request, AuthStatusRequest):
        try:
            providers = list_provider_auth_statuses()
        except AuthStoreError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return

        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthStatusResponse(
                providers=providers,
                local_secret_store=get_local_secret_store_status(),
            ),
        ).model_dump_json()
        return

    if isinstance(request, AuthSetRequest):
        try:
            status = set_provider_secret(
                request.payload.provider,
                request.payload.secret,
                storage=request.payload.storage,
            )
        except ProviderSecretValidationError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidRequest",
                message=str(error),
            ).model_dump_json()
            return
        except AuthStoreError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return

        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthSetResponse(status=status),
        ).model_dump_json()
        return

    if isinstance(request, AuthClearRequest):
        try:
            status = clear_provider_secret(request.payload.provider)
        except AuthStoreError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return

        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthClearResponse(status=status),
        ).model_dump_json()
        return

    if isinstance(request, RunEnqueueRequest):
        if request.payload.prompt.strip() == "":
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidRequest",
                message="Follow-up prompt must not be blank",
            ).model_dump_json()
            return
        try:
            queued_count = await _FOLLOW_UP_STATE.enqueue(
                request.payload.session_id,
                request.payload.prompt,
            )
        except RuntimeError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidRequest",
                message=str(error),
            ).model_dump_json()
            return
        yield RpcResponseEnvelope(
            id=request.id,
            response=RunEnqueueResponse(
                session_id=request.payload.session_id,
                queued_count=queued_count,
            ),
        ).model_dump_json()
        return

    if isinstance(request, SessionNameRequest):
        session_path = session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=request.payload.session_id,
        )
        if not session_path.exists():
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="UnknownSession",
                message=f"Unknown session_id: {request.payload.session_id}",
            ).model_dump_json()
            return
        try:
            name = append_session_name_to_session(
                path=session_path,
                workspace_root=workspace_root,
                name=request.payload.name,
            )
        except SessionNameValidationError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidRequest",
                message=str(error),
            ).model_dump_json()
            return
        except SessionFormatError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidSession",
                message=str(error),
            ).model_dump_json()
            return

        yield RpcResponseEnvelope(
            id=request.id,
            response=SessionNameResponse(
                session_id=request.payload.session_id,
                name=name,
            ),
        ).model_dump_json()
        return

    if isinstance(request, SessionPreviewRequest):
        session_path = session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=request.payload.session_id,
        )
        if not session_path.exists():
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="UnknownSession",
                message=f"Unknown session_id: {request.payload.session_id}",
            ).model_dump_json()
            return
        try:
            preview = build_session_preview(
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

        yield RpcResponseEnvelope(
            id=request.id,
            response=SessionPreviewResponse(
                session_id=preview.session_id,
                entries=preview.entries,
                truncated=preview.truncated,
            ),
        ).model_dump_json()
        return

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
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
                compacted_through_run_id=compaction.compacted_through_run_id,
            ),
        ).model_dump_json()
        return

    assert isinstance(request, RunStartRequest)
    await _FOLLOW_UP_STATE.activate(request.payload.session_id)
    try:
        prompt = request.payload.prompt
        while True:
            try:
                async for event in stream_session_run_events(
                    model=model,
                    workspace_root=workspace_root,
                    session_path=session_path,
                    prompt=prompt,
                    tool_names=CANONICAL_TOOL_NAMES,
                    thinking=request.payload.thinking,
                ):
                    yield RpcEventEnvelope(
                        id=request.id,
                        event=event,
                    ).model_dump_json()
            except SessionFormatError as error:
                yield RpcErrorEnvelope(
                    id=request.id,
                    error_type="InvalidSession",
                    message=str(error),
                ).model_dump_json()
                return
            except ProviderReadinessError as error:
                yield RpcErrorEnvelope(
                    id=request.id,
                    error_type="ProviderNotReady",
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

            prompt = await _FOLLOW_UP_STATE.take_next(request.payload.session_id)
            if prompt is None:
                return
    finally:
        await _FOLLOW_UP_STATE.deactivate(request.payload.session_id)


async def serve_rpc_stdio(
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    model: Any,
    workspace_root: Path | str,
    sessions_root: Path | str,
) -> None:
    write_lock = asyncio.Lock()
    session_locks: dict[str, asyncio.Lock] = {}
    pending_tasks: set[asyncio.Task[None]] = set()

    async def process_line(line: str) -> None:
        session_id = _extract_session_id_for_serialization(line)
        session_lock = (
            session_locks.setdefault(session_id, asyncio.Lock())
            if session_id is not None
            else None
        )

        async def write_response(response_line: str) -> None:
            async with write_lock:
                output_stream.write(response_line)
                output_stream.write("\n")
                output_stream.flush()

        async def stream_responses() -> None:
            async for response_line in handle_rpc_json_line(
                line=line,
                model=model,
                workspace_root=workspace_root,
                sessions_root=sessions_root,
            ):
                await write_response(response_line)

        if session_lock is None:
            await stream_responses()
            return

        async with session_lock:
            await stream_responses()

    while True:
        line = input_stream.readline()
        if line == "":
            break

        task = asyncio.create_task(process_line(line))
        pending_tasks.add(task)
        task.add_done_callback(pending_tasks.discard)

    if pending_tasks:
        await asyncio.gather(*pending_tasks)


def _extract_request_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        request_id = payload.get("id")
        if isinstance(request_id, str):
            return request_id

    return None


def _extract_session_id_for_serialization(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    command = payload.get("command")
    if command not in {
        "run.start",
        "session.compact",
        "session.name",
        "session.preview",
    }:
        return None
    request_payload = payload.get("payload")
    if not isinstance(request_payload, dict):
        return None
    session_id = request_payload.get("session_id")
    if isinstance(session_id, str):
        return session_id
    return None
