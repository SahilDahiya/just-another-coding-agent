from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Any, Callable, TextIO

from pydantic import TypeAdapter, ValidationError

import just_another_coding_agent.onboarding as onboarding_domain
import just_another_coding_agent.rpc.state as rpc_state
from just_another_coding_agent.contracts.code_mode import CODE_MODE_TOOL_NAMES
from just_another_coding_agent.contracts.onboarding import (
    OnboardingAnswerResult,
    OnboardingQuestionRequest,
)
from just_another_coding_agent.contracts.rpc import (
    ApprovalSubmitRequest,
    AuthClearRequest,
    AuthLoginOpenAICodexCompleteRequest,
    AuthLoginOpenAICodexStartRequest,
    AuthLoginOpenAICodexWaitRequest,
    AuthPrepareFileRequest,
    AuthSetRequest,
    AuthStatusRequest,
    ModelCatalogRequest,
    OnboardingProjectDoc,
    OnboardingStartRequest,
    OnboardingStartResponse,
    OnboardingSubmitRequest,
    OnboardingSubmitResponse,
    PermissionGetRequest,
    PermissionSetRequest,
    RpcErrorEnvelope,
    RpcEventEnvelope,
    RpcRequest,
    RpcResponseEnvelope,
    RunEnqueueRequest,
    RunEnqueueResponse,
    RunInterruptRequest,
    RunInterruptResponse,
    RunStartRequest,
    RunStartResponse,
    SessionCompactRequest,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionModeSetRequest,
    SessionNameRequest,
    SessionPreviewRequest,
    TraceLogfireStatusRequest,
    WorkspaceProjectDoc,
    WorkspaceProjectDocsRequest,
    WorkspaceTrustAcceptRequest,
    WorkspaceTrustStatusRequest,
)
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    SessionLifecycleEvent,
    SessionQueuedPromptBatchSubmittedEvent,
    SessionQueueStateEvent,
)
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalRequest,
)
from just_another_coding_agent.onboarding import (
    OnboardingAttemptNotFoundError,
    OnboardingGenerationError,
    OnboardingValidationError,
    abandon_pending_onboarding_attempt,
    start_onboarding_mcq,
    submit_onboarding_mcq,
)
from just_another_coding_agent.provider_readiness import ProviderReadinessError
from just_another_coding_agent.rpc.context import (
    RpcHandler,
    _rpc_error_handler,
    _RpcContext,
    _RpcErrorMapping,
)
from just_another_coding_agent.rpc.handlers.auth import (
    _handle_auth_clear,
    _handle_auth_login_openai_codex_complete,
    _handle_auth_login_openai_codex_start,
    _handle_auth_login_openai_codex_wait,
    _handle_auth_prepare_file,
    _handle_auth_set,
    _handle_auth_status,
    _handle_trace_logfire_status,
    _prune_stale_login_flows,
)
from just_another_coding_agent.rpc.handlers.catalog import _handle_model_catalog
from just_another_coding_agent.rpc.handlers.permissions import (
    _handle_approval_submit,
    _handle_permission_get,
    _handle_permission_set,
)
from just_another_coding_agent.rpc.handlers.sessions import (
    _handle_session_compact,
    _handle_session_mode_set,
    _handle_session_name,
    _handle_session_preview,
)
from just_another_coding_agent.rpc.handlers.workspace import (
    _handle_workspace_project_docs,
    _handle_workspace_trust_accept,
    _handle_workspace_trust_status,
    _workspace_is_trusted,
    _workspace_project_docs_root,
    _workspace_untrusted_error,
)
from just_another_coding_agent.rpc.session_store import (
    create_session,
    session_path_for_id,
)
from just_another_coding_agent.rpc.state import (
    _PendingApprovalState,
    _PendingOnboardingQuestionState,
)
from just_another_coding_agent.runtime.project_docs import (
    load_workspace_project_docs,
)
from just_another_coding_agent.runtime.session import stream_session_run_events
from just_another_coding_agent.session import (
    SessionFormatError,
    read_session_metadata,
    update_session_mode,
)
from just_another_coding_agent.tools.deps import SessionPermissionMemory
from just_another_coding_agent.tools.registry import resolve_tool_names_for_run_mode

_RPC_REQUEST_ADAPTER = TypeAdapter(RpcRequest)


def _combine_prompt_batch(prompts: list[str]) -> str:
    return "\n\n".join(prompts)


def _create_session_with_project_docs(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> tuple[str, list[WorkspaceProjectDoc]]:
    session_id = create_session(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        project_docs_root=_workspace_project_docs_root(workspace_root),
    )
    rpc_state._RUNTIME_STATE.permission_states[session_id] = (
        rpc_state._SessionPermissionContext(
            permission_state=rpc_state._get_or_create_permission_context(
                None
            ).permission_state.model_copy(deep=True),
            permission_memory=SessionPermissionMemory(),
        )
    )
    project_docs = [
        WorkspaceProjectDoc(
            path=str(doc.path),
            filename=doc.filename,
            truncated=doc.truncated,
        )
        for doc in load_workspace_project_docs(
            _workspace_project_docs_root(workspace_root)
        )
    ]
    return session_id, project_docs


async def _handle_session_create(
    request: SessionCreateRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if not _workspace_is_trusted(ctx.workspace_root):
        yield _workspace_untrusted_error(
            request_id=request.id,
            workspace_root=ctx.workspace_root,
        ).model_dump_json()
        return
    session_id, project_docs = _create_session_with_project_docs(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
    )
    yield RpcResponseEnvelope(
        id=request.id,
        response=SessionCreateResponse(
            session_id=session_id,
            project_docs=project_docs,
        ),
    ).model_dump_json()


async def _handle_run_enqueue(
    request: RunEnqueueRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    if request.payload.prompt.strip() == "":
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidRequest",
            message="Follow-up prompt must not be blank",
        ).model_dump_json()
        return
    try:
        queued_count = await rpc_state._RUNTIME_STATE.follow_up_state.enqueue(
            request.payload.session_id,
            request.payload.prompt,
            mode=request.payload.mode,
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


async def _handle_run_interrupt(
    request: RunInterruptRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
    try:
        promoted_count = await rpc_state._RUNTIME_STATE.follow_up_state.interrupt(
            request.payload.session_id,
            promote_queued_steer=request.payload.promote_queued_steer,
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
        response=RunInterruptResponse(
            session_id=request.payload.session_id,
            promoted_count=promoted_count,
        ),
    ).model_dump_json()


@_rpc_error_handler(
    _RpcErrorMapping(OnboardingGenerationError, "InvalidRequest"),
    _RpcErrorMapping(OnboardingValidationError, "InvalidRequest"),
    _RpcErrorMapping(ProviderReadinessError, "ProviderNotReady"),
)
async def _handle_onboarding_start(
    request: OnboardingStartRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if not _workspace_is_trusted(ctx.workspace_root):
        yield _workspace_untrusted_error(
            request_id=request.id,
            workspace_root=ctx.workspace_root,
        ).model_dump_json()
        return

    created_session = False
    project_docs: list[WorkspaceProjectDoc] = []
    session_id = request.payload.session_id
    generated_question = None
    if session_id is None:
        try:
            generated_question = onboarding_domain.generate_onboarding_mcq(
                workspace_root=ctx.workspace_root,
                model=ctx.model,
            )
        except ProviderReadinessError:
            raise
        except RuntimeError as error:
            raise OnboardingGenerationError(str(error)) from error
        created_session = True
        session_id, project_docs = _create_session_with_project_docs(
            sessions_root=ctx.sessions_root,
            workspace_root=ctx.workspace_root,
        )
    else:
        session_path = session_path_for_id(
            sessions_root=ctx.sessions_root,
            workspace_root=ctx.workspace_root,
            session_id=session_id,
        )
        if not session_path.exists():
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="UnknownSession",
                message=f"Unknown session_id: {session_id}",
            ).model_dump_json()
            return

    try:
        result = start_onboarding_mcq(
            sessions_root=ctx.sessions_root,
            workspace_root=ctx.workspace_root,
            session_id=session_id,
            model=ctx.model,
            created_session=created_session,
            generated_question=generated_question,
        )
    except Exception:
        if created_session:
            _cleanup_created_session_artifacts(
                sessions_root=ctx.sessions_root,
                workspace_root=ctx.workspace_root,
                session_id=session_id,
            )
        raise
    yield RpcResponseEnvelope(
        id=request.id,
        response=OnboardingStartResponse(
            session_id=result.session_id,
            created_session=result.created_session,
            project_docs=[
                OnboardingProjectDoc(
                    path=doc.path,
                    filename=doc.filename,
                    truncated=doc.truncated,
                )
                for doc in project_docs
            ],
            attempt_id=result.attempt_id,
            question_type=result.question_type,
            snippet_path=result.snippet.path,
            snippet_start_line=result.snippet.start_line,
            snippet_end_line=result.snippet.end_line,
            snippet_text=result.snippet.text,
            prompt=result.prompt,
            options=list(result.options),
            explanation=result.explanation,
            generator_version=result.generator_version,
        ),
    ).model_dump_json()


def _cleanup_created_session_artifacts(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str | None,
) -> None:
    if session_id is None:
        return
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    session_path.unlink(missing_ok=True)
    session_path.with_suffix(".meta.json").unlink(missing_ok=True)
    rpc_state._RUNTIME_STATE.permission_states.pop(session_id, None)


@_rpc_error_handler(
    _RpcErrorMapping(OnboardingAttemptNotFoundError, "InvalidRequest"),
    _RpcErrorMapping(OnboardingValidationError, "InvalidRequest"),
)
async def _handle_onboarding_submit(
    request: OnboardingSubmitRequest,
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

    result = submit_onboarding_mcq(
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
        session_id=request.payload.session_id,
        attempt_id=request.payload.attempt_id,
        selected_index=request.payload.selected_index,
    )
    pending = rpc_state._RUNTIME_STATE.pending_onboarding_questions.get(
        request.payload.session_id,
        {}
    )
    question_state = pending.get(request.payload.attempt_id)
    if question_state is not None and not question_state.response_future.done():
        asyncio.get_running_loop().call_soon(
            question_state.response_future.set_result,
            result,
        )
    yield RpcResponseEnvelope(
        id=request.id,
        response=OnboardingSubmitResponse(
            session_id=result.session_id,
            attempt_id=result.attempt_id,
            question_type=result.question_type,
            selected_index=result.selected_index,
            correct_index=result.correct_index,
            correct_option=result.correct_option,
            is_correct=result.is_correct,
            explanation=result.explanation,
        ),
    ).model_dump_json()


async def _handle_run_start(
    request: RunStartRequest,
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
        session_metadata = read_session_metadata(
            path=session_path.with_suffix(".meta.json")
        )
    except SessionFormatError as error:
        yield RpcErrorEnvelope(
            id=request.id,
            error_type="InvalidSession",
            message=str(error),
        ).model_dump_json()
        return
    effective_run_mode = (
        request.payload.mode
        if request.payload.mode is not None
        else session_metadata.current_mode
    )
    if session_metadata.current_mode != effective_run_mode:
        try:
            update_session_mode(
                path=session_path,
                current_mode=effective_run_mode,
            )
        except SessionFormatError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidSession",
                message=str(error),
            ).model_dump_json()
            return
    if request.payload.code_mode_tools_only:
        tool_names = CODE_MODE_TOOL_NAMES
    else:
        tool_names = resolve_tool_names_for_run_mode(effective_run_mode)
    if request.payload.enable_code_mode and not request.payload.code_mode_tools_only:
        tool_names = (*tool_names, *CODE_MODE_TOOL_NAMES)

    current_task = asyncio.current_task()
    if current_task is None:
        raise RuntimeError("run.start must execute inside an asyncio task")
    emit_rpc_event = ctx.emit_rpc_event
    if emit_rpc_event is None:
        raise RuntimeError("run.start requires an rpc event emitter")

    async def emit_queue_state(event: SessionQueueStateEvent) -> None:
        await emit_rpc_event(request.id, event)

    async def emit_submitted_prompt_batch(
        mode: str,
        prompts: list[str],
    ) -> None:
        if not prompts:
            return
        await emit_rpc_event(
            request.id,
            SessionQueuedPromptBatchSubmittedEvent(mode=mode, prompts=prompts),
        )

    async def activate_boundary(
        attach: Callable[[list[str]], None],
    ) -> None:
        await rpc_state._RUNTIME_STATE.follow_up_state.activate_steer_boundary(
            request.payload.session_id,
            attach,
        )

    async def submit_boundary() -> None:
        await rpc_state._RUNTIME_STATE.follow_up_state.submit_active_steer_boundary(
            request.payload.session_id
        )

    async def resolve_approval_request(
        decision_request: ApprovalRequest,
    ) -> ApprovalDecision:
        approval_state = _PendingApprovalState(
            request_id=decision_request.request_id,
            request=decision_request,
            response_future=(
                asyncio.get_running_loop().create_future()
            ),
        )
        session_pending = rpc_state._RUNTIME_STATE.pending_approvals[
            request.payload.session_id
        ]
        if decision_request.request_id in session_pending:
            raise RuntimeError(
                "Approval request already pending for session: "
                f"{decision_request.request_id}"
            )
        session_pending[decision_request.request_id] = approval_state
        try:
            return await approval_state.response_future
        finally:
            current_pending = rpc_state._RUNTIME_STATE.pending_approvals.get(
                request.payload.session_id
            )
            if current_pending is not None:
                current_pending.pop(decision_request.request_id, None)
                if not current_pending:
                    rpc_state._RUNTIME_STATE.pending_approvals.pop(
                        request.payload.session_id, None
                    )

    async def resolve_onboarding_question(
        question_request: OnboardingQuestionRequest,
    ) -> OnboardingAnswerResult:
        question_state = _PendingOnboardingQuestionState(
            attempt_id=question_request.attempt_id,
            question=question_request,
            response_future=asyncio.get_running_loop().create_future(),
        )
        session_pending = rpc_state._RUNTIME_STATE.pending_onboarding_questions[
            request.payload.session_id
        ]
        if question_request.attempt_id in session_pending:
            raise RuntimeError(
                "Onboarding question already pending for session: "
                f"{question_request.attempt_id}"
            )
        session_pending[question_request.attempt_id] = question_state
        try:
            return await question_state.response_future
        except asyncio.CancelledError:
            abandon_pending_onboarding_attempt(
                sessions_root=ctx.sessions_root,
                workspace_root=ctx.workspace_root,
                session_id=request.payload.session_id,
                attempt_id=question_request.attempt_id,
            )
            raise
        finally:
            current_pending = rpc_state._RUNTIME_STATE.pending_onboarding_questions.get(
                request.payload.session_id
            )
            if current_pending is not None:
                current_pending.pop(question_request.attempt_id, None)
                if not current_pending:
                    rpc_state._RUNTIME_STATE.pending_onboarding_questions.pop(
                        request.payload.session_id, None
                    )

    await rpc_state._RUNTIME_STATE.follow_up_state.activate(
        request.payload.session_id,
        run_task=current_task,
        emit_queue_state=emit_queue_state,
        emit_submitted_prompt_batch=emit_submitted_prompt_batch,
    )
    try:
        prompt = request.payload.prompt
        while True:
            try:
                async for event in stream_session_run_events(
                    model=ctx.model,
                    workspace_root=ctx.workspace_root,
                    session_path=session_path,
                    prompt=prompt,
                    tool_names=tool_names,
                    run_mode=effective_run_mode,
                    thinking=request.payload.thinking,
                    permission_state=rpc_state._get_or_create_permission_context(
                        request.payload.session_id
                    ).permission_state,
                    permission_memory=rpc_state._get_or_create_permission_context(
                        request.payload.session_id
                    ).permission_memory,
                    resolve_approval_request=resolve_approval_request,
                    resolve_onboarding_question=resolve_onboarding_question,
                    activate_steer_boundary=activate_boundary,
                    submit_steer_boundary=submit_boundary,
                    deactivate_steer_boundary=lambda: (
                        rpc_state._RUNTIME_STATE.follow_up_state.deactivate_steer_boundary(
                            request.payload.session_id
                        )
                    ),
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
            except asyncio.CancelledError:
                # Consume the cancellation so the follow-up batch check
                # can actually run. Without this, Python 3.11+ keeps the
                # cancelling flag set and the very next `await`
                # re-raises CancelledError, which would bypass the
                # handoff to a queued follow-up batch.
                current_task = asyncio.current_task()
                if current_task is not None:
                    current_task.uncancel()
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

            follow_up_state = rpc_state._RUNTIME_STATE.follow_up_state
            await follow_up_state.downgrade_pending_steers_to_follow_ups(
                request.payload.session_id
            )
            prompt_batch = await follow_up_state.take_next_follow_up_batch(
                request.payload.session_id
            )
            if prompt_batch is None:
                break
            await emit_submitted_prompt_batch("later", prompt_batch)
            prompt = _combine_prompt_batch(prompt_batch)
    finally:
        await rpc_state._RUNTIME_STATE.follow_up_state.deactivate(
            request.payload.session_id
        )
    yield RpcResponseEnvelope(
        id=request.id,
        response=RunStartResponse(session_id=request.payload.session_id),
    ).model_dump_json()


_RPC_HANDLERS: dict[type[Any], RpcHandler] = {
    SessionCreateRequest: _handle_session_create,
    ModelCatalogRequest: _handle_model_catalog,
    AuthStatusRequest: _handle_auth_status,
    AuthPrepareFileRequest: _handle_auth_prepare_file,
    AuthSetRequest: _handle_auth_set,
    AuthClearRequest: _handle_auth_clear,
    AuthLoginOpenAICodexStartRequest: _handle_auth_login_openai_codex_start,
    AuthLoginOpenAICodexWaitRequest: _handle_auth_login_openai_codex_wait,
    AuthLoginOpenAICodexCompleteRequest: _handle_auth_login_openai_codex_complete,
    TraceLogfireStatusRequest: _handle_trace_logfire_status,
    RunEnqueueRequest: _handle_run_enqueue,
    RunInterruptRequest: _handle_run_interrupt,
    OnboardingStartRequest: _handle_onboarding_start,
    OnboardingSubmitRequest: _handle_onboarding_submit,
    PermissionGetRequest: _handle_permission_get,
    PermissionSetRequest: _handle_permission_set,
    ApprovalSubmitRequest: _handle_approval_submit,
    SessionNameRequest: _handle_session_name,
    SessionPreviewRequest: _handle_session_preview,
    SessionModeSetRequest: _handle_session_mode_set,
    WorkspaceProjectDocsRequest: _handle_workspace_project_docs,
    WorkspaceTrustStatusRequest: _handle_workspace_trust_status,
    WorkspaceTrustAcceptRequest: _handle_workspace_trust_accept,
    SessionCompactRequest: _handle_session_compact,
    RunStartRequest: _handle_run_start,
}


async def handle_rpc_json_line(
    *,
    line: str,
    model: Any,
    workspace_root: Path | str,
    sessions_root: Path | str,
    emit_rpc_event: Callable[[str, RunEvent | SessionLifecycleEvent], Awaitable[None]]
    | None = None,
) -> AsyncIterator[str]:
    _prune_stale_login_flows()

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

    ctx = _RpcContext(
        model=model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        emit_rpc_event=emit_rpc_event,
    )
    handler = _RPC_HANDLERS.get(type(request))
    if handler is None:
        yield RpcErrorEnvelope(
            id=request_id,
            error_type="InvalidRequest",
            message="Invalid RPC request",
        ).model_dump_json()
        return

    async for response_line in handler(request, ctx):
        yield response_line


async def serve_rpc_stdio(
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    model: Any,
    workspace_root: Path | str,
    sessions_root: Path | str,
) -> None:
    session_locks: dict[str, asyncio.Lock] = {}
    pending_tasks: set[asyncio.Task[None]] = set()
    input_is_memory_stream = isinstance(input_stream, io.StringIO)
    output_is_memory_stream = isinstance(output_stream, io.StringIO)
    output_executor = (
        None
        if output_is_memory_stream
        else concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jaca-stdio-out"
        )
    )
    loop = asyncio.get_running_loop()

    # Unbounded outbound queue + single dedicated writer task. This makes
    # write_response non-blocking and non-yielding from the producers'
    # point of view (the asyncio.Queue.put on an unbounded queue is
    # synchronous internally), so producer ordering is preserved exactly
    # as it would be with a sync write. The actual sync write happens in
    # a worker thread so that a full or slow pipe (notably the small
    # ~4KB Windows anonymous pipe between the python backend and the Go
    # TUI) only blocks the writer thread, never the event loop.
    output_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _sync_drain(lines: list[str]) -> None:
        for line in lines:
            output_stream.write(line)
            output_stream.write("\n")
        output_stream.flush()

    async def _output_writer() -> None:
        while True:
            first = await output_queue.get()
            if first is None:
                return
            batch: list[str] = [first]
            # Drain any additional ready items so we issue one
            # bigger write rather than many tiny ones.
            while True:
                try:
                    nxt = output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    if output_is_memory_stream:
                        _sync_drain(batch)
                    else:
                        await loop.run_in_executor(output_executor, _sync_drain, batch)
                    return
                batch.append(nxt)
            try:
                if output_is_memory_stream:
                    _sync_drain(batch)
                else:
                    await loop.run_in_executor(output_executor, _sync_drain, batch)
            except Exception:
                # Never let a broken-pipe condition kill the writer
                # task; the rest of the backend should still drain
                # cleanly.
                pass

    writer_task: asyncio.Task[None] = asyncio.create_task(_output_writer())

    async def process_line(line: str) -> None:
        session_id = _extract_session_id_for_serialization(line)
        session_lock = (
            session_locks.setdefault(session_id, asyncio.Lock())
            if session_id is not None
            else None
        )

        async def write_response(response_line: str) -> None:
            # Non-blocking enqueue. Order is preserved because put on an
            # unbounded asyncio.Queue is synchronous and there is exactly
            # one consumer (the writer task).
            output_queue.put_nowait(response_line)

        async def emit_rpc_event(
            request_id: str,
            event: RunEvent | SessionLifecycleEvent,
        ) -> None:
            await write_response(
                RpcEventEnvelope(
                    id=request_id,
                    event=event,
                ).model_dump_json()
            )

        async def stream_responses() -> None:
            async for response_line in handle_rpc_json_line(
                line=line,
                model=model,
                workspace_root=workspace_root,
                sessions_root=sessions_root,
                emit_rpc_event=emit_rpc_event,
            ):
                await write_response(response_line)

        if session_lock is None:
            await stream_responses()
            return

        async with session_lock:
            await stream_responses()

    try:
        while True:
            if input_is_memory_stream:
                line = input_stream.readline()
            else:
                line = await asyncio.to_thread(input_stream.readline)
            if line == "":
                break

            task = asyncio.create_task(process_line(line))
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)

        if pending_tasks:
            await asyncio.gather(*pending_tasks)
    finally:
        # Signal the writer task to drain remaining items and exit, then
        # await it so any final response lines reach the pipe before the
        # backend returns.
        output_queue.put_nowait(None)
        try:
            await writer_task
        except Exception:
            pass
        if output_executor is not None:
            output_executor.shutdown(wait=True, cancel_futures=True)


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
        "onboarding.start",
    }:
        return None
    request_payload = payload.get("payload")
    if not isinstance(request_payload, dict):
        return None
    session_id = request_payload.get("session_id")
    if isinstance(session_id, str):
        return session_id
    return None
