from __future__ import annotations

import json
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
    RunStartRequest,
    SessionCompactRequest,
    SessionCompactResponse,
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
from just_another_coding_agent.session import SessionFormatError

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
                first_kept_run_id=compaction.first_kept_run_id,
                summary=compaction.summary,
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
        line = input_stream.readline()
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
