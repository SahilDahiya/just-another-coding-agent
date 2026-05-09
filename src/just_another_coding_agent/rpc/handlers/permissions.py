from __future__ import annotations

from collections.abc import AsyncIterator

import just_another_coding_agent.rpc.state as rpc_state
from just_another_coding_agent.contracts.rpc import (
    ApprovalSubmitRequest,
    ApprovalSubmitResponse,
    PermissionGetRequest,
    PermissionGetResponse,
    PermissionSetRequest,
    PermissionSetResponse,
    RpcErrorEnvelope,
    RpcResponseEnvelope,
)
from just_another_coding_agent.contracts.sandbox import normalize_approval_decision
from just_another_coding_agent.rpc.context import _RpcContext
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.rpc.state import (
    _build_live_permission_state,
    _get_or_create_permission_context,
    _permission_state_key,
    _SessionPermissionContext,
)


async def _handle_permission_get(
    request: PermissionGetRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if request.payload.session_id is not None:
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

    yield RpcResponseEnvelope(
        id=request.id,
        response=PermissionGetResponse(
            session_id=request.payload.session_id,
            permission_state=_get_or_create_permission_context(
                request.payload.session_id
            ).permission_state,
        ),
    ).model_dump_json()


async def _handle_permission_set(
    request: PermissionSetRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if request.payload.session_id is not None:
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

    existing_context = _get_or_create_permission_context(
        request.payload.session_id
    )
    permission_state = _build_live_permission_state(
        sandbox_policy=(
            request.payload.sandbox_policy
            or existing_context.permission_state.sandbox_policy
        ),
        approval_policy=(
            request.payload.approval_policy
            or existing_context.permission_state.approval_policy
        ),
    )
    existing_context.permission_memory.clear()
    state_key = _permission_state_key(request.payload.session_id)
    rpc_state._RUNTIME_STATE.permission_states[state_key] = _SessionPermissionContext(
        permission_state=permission_state,
        permission_memory=existing_context.permission_memory,
    )

    yield RpcResponseEnvelope(
        id=request.id,
        response=PermissionSetResponse(
            session_id=request.payload.session_id,
            permission_state=permission_state,
        ),
    ).model_dump_json()


async def _handle_approval_submit(
    request: ApprovalSubmitRequest,
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

    pending = rpc_state._RUNTIME_STATE.pending_approvals.get(
        request.payload.session_id, {}
    )
    approval_state = pending.get(request.payload.decision.request_id)
    if approval_state is None:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message=(
                "Unknown approval request for session: "
                f"{request.payload.decision.request_id}"
            ),
        ).model_dump_json()
        return
    if approval_state.response_future.done():
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message=(
                "Approval request already resolved: "
                f"{request.payload.decision.request_id}"
            ),
        ).model_dump_json()
        return

    try:
        decision = normalize_approval_decision(
            request=approval_state.request,
            decision=request.payload.decision,
        )
    except ValueError as error:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message=str(error),
        ).model_dump_json()
        return

    approval_state.response_future.set_result(decision)
    yield RpcResponseEnvelope(
        id=request.id,
        response=ApprovalSubmitResponse(
            session_id=request.payload.session_id,
            decision=decision,
        ),
    ).model_dump_json()
