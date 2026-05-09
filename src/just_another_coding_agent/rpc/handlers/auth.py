from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from typing import Any

import just_another_coding_agent.rpc.state as rpc_state
from just_another_coding_agent.auth import (
    AuthStoreError,
    ProviderSecretValidationError,
    clear_provider_secret,
    complete_openai_codex_oauth_login,
    get_local_secret_store_status,
    get_oauth_provider_statuses,
    list_provider_auth_statuses,
    prepare_provider_secret_file,
    set_provider_secret,
    start_openai_codex_oauth_login,
    wait_for_openai_codex_oauth_login,
)
from just_another_coding_agent.contracts.rpc import (
    AuthClearRequest,
    AuthClearResponse,
    AuthLoginOpenAICodexCompleteRequest,
    AuthLoginOpenAICodexCompleteResponse,
    AuthLoginOpenAICodexStartRequest,
    AuthLoginOpenAICodexStartResponse,
    AuthLoginOpenAICodexWaitRequest,
    AuthLoginOpenAICodexWaitResponse,
    AuthPrepareFileRequest,
    AuthPrepareFileResponse,
    AuthSetRequest,
    AuthSetResponse,
    AuthStatusRequest,
    AuthStatusResponse,
    RpcErrorEnvelope,
    RpcResponseEnvelope,
    TraceLogfireStatusRequest,
    TraceLogfireStatusResponse,
)
from just_another_coding_agent.rpc.context import (
    _rpc_error_handler,
    _RpcContext,
    _RpcErrorMapping,
)
from just_another_coding_agent.rpc.state import _OpenAICodexLoginFlowState
from just_another_coding_agent.runtime.observability import logfire_setup_status

_LOGIN_FLOW_TTL_SECONDS = 15 * 60


@_rpc_error_handler(_RpcErrorMapping(AuthStoreError, "InternalError"))
async def _handle_auth_status(
    request: AuthStatusRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    providers = list_provider_auth_statuses()
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthStatusResponse(
            providers=providers,
            local_secret_store=get_local_secret_store_status(),
            oauth_providers=get_oauth_provider_statuses(),
        ),
    ).model_dump_json()


async def _handle_trace_logfire_status(
    request: TraceLogfireStatusRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = logfire_setup_status()
    yield RpcResponseEnvelope(
        id=request.id,
        response=TraceLogfireStatusResponse(
            installed=status.installed,
            credentials_configured=status.credentials_configured,
        ),
    ).model_dump_json()


@_rpc_error_handler(_RpcErrorMapping(AuthStoreError, "InternalError"))
async def _handle_auth_prepare_file(
    request: AuthPrepareFileRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    prepared = prepare_provider_secret_file(request.payload.provider)
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthPrepareFileResponse(
            provider=prepared.provider,
            env_key=prepared.env_key,
            file_path=prepared.file_path,
            created=prepared.created,
            file_snippet=prepared.file_snippet,
            entry_snippet=prepared.entry_snippet,
        ),
    ).model_dump_json()


@_rpc_error_handler(
    _RpcErrorMapping(ProviderSecretValidationError, "InvalidRequest"),
    _RpcErrorMapping(AuthStoreError, "InternalError"),
)
async def _handle_auth_set(
    request: AuthSetRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = set_provider_secret(
        request.payload.provider,
        request.payload.secret,
        storage=request.payload.storage,
    )
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthSetResponse(status=status),
    ).model_dump_json()


@_rpc_error_handler(_RpcErrorMapping(AuthStoreError, "InternalError"))
async def _handle_auth_clear(
    request: AuthClearRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = clear_provider_secret(request.payload.provider)
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthClearResponse(status=status),
    ).model_dump_json()


@_rpc_error_handler(_RpcErrorMapping(AuthStoreError, "InternalError"))
async def _handle_auth_login_openai_codex_start(
    request: AuthLoginOpenAICodexStartRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    flow, flow_id, auth_url, instructions = start_openai_codex_oauth_login()
    for state in rpc_state._RUNTIME_STATE.openai_codex_login_flows.values():
        _cancel_login_flow_task(state.task)
        _fail_login_result(state.result, "login flow cancelled")
    rpc_state._RUNTIME_STATE.openai_codex_login_flows.clear()

    result = asyncio.get_running_loop().create_future()
    rpc_state._RUNTIME_STATE.openai_codex_login_flows[flow_id] = (
        _OpenAICodexLoginFlowState(
            flow=flow,
            task=asyncio.create_task(
                _drive_login_result(
                    result=result,
                    wait_for_status=wait_for_openai_codex_oauth_login(flow),
                )
            ),
            result=result,
            started_at=time.monotonic(),
        )
    )
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthLoginOpenAICodexStartResponse(
            flow_id=flow_id,
            auth_url=auth_url,
            instructions=instructions,
        ),
    ).model_dump_json()


async def _handle_auth_login_openai_codex_wait(
    request: AuthLoginOpenAICodexWaitRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    state = rpc_state._RUNTIME_STATE.openai_codex_login_flows.get(
        request.payload.flow_id
    )
    result = state.result if state is not None else None
    if result is None:
        status = _find_oauth_provider_status("openai-codex")
        if status is not None and status.logged_in:
            yield RpcResponseEnvelope(
                id=request.id,
                response=AuthLoginOpenAICodexWaitResponse(status=status),
            ).model_dump_json()
            return
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message="unknown OpenAI Codex login flow",
        ).model_dump_json()
        return
    try:
        status = await result
    except Exception as error:
        _pop_login_flow_state(request.payload.flow_id)
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InternalError",
            message=str(error),
        ).model_dump_json()
        return
    _pop_login_flow_state(request.payload.flow_id)
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthLoginOpenAICodexWaitResponse(status=status),
    ).model_dump_json()


async def _handle_auth_login_openai_codex_complete(
    request: AuthLoginOpenAICodexCompleteRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    state = rpc_state._RUNTIME_STATE.openai_codex_login_flows.get(
        request.payload.flow_id
    )
    if state is None or state.result is None:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message="unknown OpenAI Codex login flow",
        ).model_dump_json()
        return
    try:
        status = await complete_openai_codex_oauth_login(
            state.flow,
            request.payload.callback_or_code,
        )
    except Exception as error:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InternalError",
            message=str(error),
        ).model_dump_json()
        return
    state = _pop_login_flow_state(request.payload.flow_id)
    if state is not None and state.result is not None and not state.result.done():
        state.result.set_result(status)
    _cancel_login_flow_task(state.task if state is not None else None)
    yield RpcResponseEnvelope(
        id=request.id,
        response=AuthLoginOpenAICodexCompleteResponse(status=status),
    ).model_dump_json()


def _prune_stale_login_flows(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    stale_ids = [
        flow_id
        for flow_id, state in rpc_state._RUNTIME_STATE.openai_codex_login_flows.items()
        if (
            state.started_at is not None
            and (current - state.started_at) > _LOGIN_FLOW_TTL_SECONDS
        )
    ]
    for flow_id in stale_ids:
        state = _pop_login_flow_state(flow_id)
        if state is None:
            continue
        _cancel_login_flow_task(state.task)
        _fail_login_result(state.result, "OpenAI Codex login flow expired")


def _pop_login_flow_state(flow_id: str) -> _OpenAICodexLoginFlowState | None:
    return rpc_state._RUNTIME_STATE.openai_codex_login_flows.pop(flow_id, None)


def _cancel_login_flow_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()


def _fail_login_result(result: asyncio.Future | None, message: str) -> None:
    if result is None or result.done():
        return
    result.set_exception(RuntimeError(message))


async def _drive_login_result(
    *,
    result: asyncio.Future,
    wait_for_status: Awaitable[Any],
) -> None:
    try:
        status = await wait_for_status
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if not result.done():
            result.set_exception(error)
        return
    if not result.done():
        result.set_result(status)


def _find_oauth_provider_status(provider: str):
    for status in get_oauth_provider_statuses():
        if status.provider == provider:
            return status
    return None
