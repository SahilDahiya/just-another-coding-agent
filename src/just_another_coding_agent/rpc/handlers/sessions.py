from __future__ import annotations

from collections.abc import AsyncIterator

from just_another_coding_agent.contracts.rpc import (
    RpcErrorEnvelope,
    RpcResponseEnvelope,
    SessionCompactRequest,
    SessionCompactResponse,
    SessionModeSetRequest,
    SessionModeSetResponse,
    SessionNameRequest,
    SessionNameResponse,
    SessionPreviewRequest,
    SessionPreviewResponse,
)
from just_another_coding_agent.rpc.context import _RpcContext
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.runtime.compaction import (
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.session import (
    SessionFormatError,
    SessionNameValidationError,
    append_session_name_to_session,
    build_session_preview,
    update_session_mode,
)


async def _handle_session_name(
    request: SessionNameRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    session_path = session_path_for_id(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
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
            workspace_root=ctx.workspace_root,
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


async def _handle_session_preview(
    request: SessionPreviewRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    session_path = session_path_for_id(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
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
            workspace_root=ctx.workspace_root,
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


async def _handle_session_mode_set(
    request: SessionModeSetRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    session_path = session_path_for_id(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
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
        metadata = update_session_mode(
            path=session_path,
            current_mode=request.payload.mode,
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
        response=SessionModeSetResponse(
            session_id=request.payload.session_id,
            mode=metadata.current_mode,
        ),
    ).model_dump_json()


async def _handle_session_compact(
    request: SessionCompactRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    session_path = session_path_for_id(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
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
        compaction = await summarize_and_append_compaction_to_session(
            model=ctx.model,
            path=session_path,
            workspace_root=ctx.workspace_root,
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
