from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from pydantic import TypeAdapter, ValidationError

import just_another_coding_agent.onboarding as onboarding_domain
from just_another_coding_agent.auth import (
    AuthStoreError,
    OpenAICodexLoginFlow,
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
from just_another_coding_agent.contracts.model_catalog import (
    CANONICAL_PROVIDER_ORDER,
    default_model_for_provider,
    shipped_models_for_provider,
)
from just_another_coding_agent.contracts.onboarding import (
    OnboardingAnswerResult,
    OnboardingQuestionRequest,
)
from just_another_coding_agent.contracts.rpc import (
    ApprovalSubmitRequest,
    ApprovalSubmitResponse,
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
    ModelCatalogModel,
    ModelCatalogProvider,
    ModelCatalogRequest,
    ModelCatalogResponse,
    OnboardingProjectDoc,
    OnboardingStartRequest,
    OnboardingStartResponse,
    OnboardingSubmitRequest,
    OnboardingSubmitResponse,
    PermissionGetRequest,
    PermissionGetResponse,
    PermissionSetRequest,
    PermissionSetResponse,
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
    SessionCompactResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionNameRequest,
    SessionNameResponse,
    SessionPreviewRequest,
    SessionPreviewResponse,
    TraceLogfireStatusRequest,
    TraceLogfireStatusResponse,
    WorkspaceProjectDoc,
    WorkspaceProjectDocsRequest,
    WorkspaceProjectDocsResponse,
    WorkspaceTrustAcceptRequest,
    WorkspaceTrustAcceptResponse,
    WorkspaceTrustStatusRequest,
    WorkspaceTrustStatusResponse,
)
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    SessionLifecycleEvent,
    SessionQueuedPromptBatchSubmittedEvent,
    SessionQueueStateEvent,
)
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    DangerFullAccessSandboxPolicy,
    EffectiveCapabilities,
    PermissionState,
    SandboxPolicy,
    build_default_permission_state,
    build_permission_state,
    normalize_approval_decision,
)
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.onboarding import (
    OnboardingAttemptNotFoundError,
    OnboardingGenerationError,
    OnboardingValidationError,
    abandon_pending_onboarding_attempt,
    start_onboarding_mcq,
    submit_onboarding_mcq,
)
from just_another_coding_agent.provider_readiness import ProviderReadinessError
from just_another_coding_agent.rpc.session_store import (
    create_session,
    session_path_for_id,
)
from just_another_coding_agent.runtime.compaction import (
    summarize_and_append_compaction_to_session,
)
from just_another_coding_agent.runtime.observability import logfire_setup_status
from just_another_coding_agent.runtime.project_docs import (
    load_workspace_project_docs,
)
from just_another_coding_agent.runtime.session import stream_session_run_events
from just_another_coding_agent.runtime.workspace_trust import (
    accept_workspace_trust,
    resolve_workspace_trust_target,
    workspace_trust_status,
)
from just_another_coding_agent.session import (
    SessionFormatError,
    SessionNameValidationError,
    append_session_name_to_session,
    build_session_preview,
)
from just_another_coding_agent.tools.deps import SessionPermissionMemory

_RPC_REQUEST_ADAPTER = TypeAdapter(RpcRequest)
_LOGIN_FLOW_TTL_SECONDS = 15 * 60


@dataclass
class _QueuedPromptBatch:
    kind: str
    prompts: list[str]


class _FollowUpState:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_sessions: set[str] = set()
        self._active_run_tasks: dict[str, asyncio.Task[None]] = {}
        self._follow_up_queues: dict[str, deque[_QueuedPromptBatch]] = defaultdict(
            deque
        )
        self._steer_queues: dict[str, deque[str]] = defaultdict(deque)
        self._active_steer_targets: dict[str, Callable[[list[str]], None]] = {}
        self._queue_event_emitters: dict[
            str, Callable[[SessionQueueStateEvent], Awaitable[None]]
        ] = {}
        self._submitted_prompt_emitters: dict[
            str, Callable[[str, list[str]], Awaitable[None]]
        ] = {}

    async def activate(
        self,
        session_id: str,
        *,
        run_task: asyncio.Task[None],
        emit_queue_state: Callable[[SessionQueueStateEvent], Awaitable[None]],
        emit_submitted_prompt_batch: Callable[[str, list[str]], Awaitable[None]]
        | None = None,
    ) -> None:
        async with self._lock:
            self._active_sessions.add(session_id)
            self._active_run_tasks[session_id] = run_task
            self._queue_event_emitters[session_id] = emit_queue_state
            if emit_submitted_prompt_batch is not None:
                self._submitted_prompt_emitters[session_id] = (
                    emit_submitted_prompt_batch
                )
            event = self._build_queue_state_event_locked(session_id)
        if event.next_prompts or event.later_prompts:
            await emit_queue_state(event)

    async def deactivate(self, session_id: str) -> None:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            previous_event = self._build_queue_state_event_locked(session_id)
            self._active_sessions.discard(session_id)
            self._active_run_tasks.pop(session_id, None)
            self._active_steer_targets.pop(session_id, None)
            emitter = self._queue_event_emitters.pop(session_id, None)
            self._submitted_prompt_emitters.pop(session_id, None)
            if emitter is not None and (
                previous_event.next_prompts or previous_event.later_prompts
            ):
                event = SessionQueueStateEvent(next_prompts=[], later_prompts=[])
            if not self._follow_up_queues.get(session_id):
                self._follow_up_queues.pop(session_id, None)
            if not self._steer_queues.get(session_id):
                self._steer_queues.pop(session_id, None)
        if emitter is not None and event is not None:
            await emitter(event)

    async def enqueue(
        self,
        session_id: str,
        prompt: str,
        *,
        mode: str,
    ) -> int:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            if session_id not in self._active_sessions:
                raise RuntimeError("Queueing requires an active run for this session.")
            previous_event = self._build_queue_state_event_locked(session_id)
            if mode == "next":
                queue = self._steer_queues[session_id]
                queue.append(prompt)
                queued_count = len(queue)
                emitter = self._queue_event_emitters.get(session_id)
                event = self._build_queue_state_event_locked(session_id)
            else:
                queued_count = self._append_follow_up_locked(
                    session_id,
                    prompt=prompt,
                    kind="later",
                )
                emitter = self._queue_event_emitters.get(session_id)
                event = self._build_queue_state_event_locked(session_id)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)
        return queued_count

    async def activate_steer_boundary(
        self,
        session_id: str,
        attach: Callable[[list[str]], None],
    ) -> None:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            previous_event = self._build_queue_state_event_locked(session_id)
            if self._active_steer_targets.get(session_id) is not None:
                raise RuntimeError("Steer boundary already active for session")
            self._active_steer_targets[session_id] = attach
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)

    async def submit_active_steer_boundary(self, session_id: str) -> None:
        attach: Callable[[list[str]], None] | None = None
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        submitted_emitter: Callable[[str, list[str]], Awaitable[None]] | None = None
        submitted_prompts: list[str] = []
        async with self._lock:
            previous_event = self._build_queue_state_event_locked(session_id)
            attach = self._active_steer_targets.get(session_id)
            if attach is None:
                raise RuntimeError("Steer boundary is not active for session")
            queue = self._steer_queues.get(session_id)
            if queue:
                while queue:
                    submitted_prompts.append(queue.popleft())
                self._steer_queues.pop(session_id, None)
                attach(list(submitted_prompts))
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
            submitted_emitter = self._submitted_prompt_emitters.get(session_id)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)
        if submitted_emitter is not None and submitted_prompts:
            await submitted_emitter("next", submitted_prompts)

    async def deactivate_steer_boundary(self, session_id: str) -> None:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            previous_event = self._build_queue_state_event_locked(session_id)
            self._active_steer_targets.pop(session_id, None)
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)

    async def downgrade_pending_steers_to_follow_ups(self, session_id: str) -> None:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            previous_event = self._build_queue_state_event_locked(session_id)
            prompts = self._drain_pending_steers_locked(session_id)
            if not prompts:
                return
            self._prepend_follow_ups_locked(session_id, prompts)
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)

    async def take_next_follow_up_batch(self, session_id: str) -> list[str] | None:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            queue = self._follow_up_queues.get(session_id)
            if not queue:
                return None
            previous_event = self._build_queue_state_event_locked(session_id)
            batch = queue.popleft()
            if not queue:
                self._follow_up_queues.pop(session_id, None)
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
            prompts = list(batch.prompts)
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)
        return prompts

    async def interrupt(
        self,
        session_id: str,
        *,
        promote_queued_steer: bool,
    ) -> int:
        emitter: Callable[[SessionQueueStateEvent], Any] | None = None
        event: SessionQueueStateEvent | None = None
        async with self._lock:
            if session_id not in self._active_sessions:
                raise RuntimeError("Interrupt requires an active run for this session.")
            previous_event = self._build_queue_state_event_locked(session_id)
            run_task = self._active_run_tasks.get(session_id)
            if run_task is None:
                raise RuntimeError("Interrupt requires an active run for this session.")
            promoted_count = 0
            if promote_queued_steer:
                prompts = self._drain_pending_steers_locked(session_id)
                promoted_count = len(prompts)
                if prompts:
                    self._prepend_follow_ups_locked(session_id, prompts)
            emitter = self._queue_event_emitters.get(session_id)
            event = self._build_queue_state_event_locked(session_id)
            run_task.cancel()
        if emitter is not None and event is not None and previous_event != event:
            await emitter(event)
        return promoted_count

    def _drain_pending_steers_locked(self, session_id: str) -> list[str]:
        prompts: list[str] = []
        queue = self._steer_queues.get(session_id)
        if queue:
            while queue:
                prompts.append(queue.popleft())
            self._steer_queues.pop(session_id, None)
        return prompts

    def _prepend_follow_ups_locked(
        self,
        session_id: str,
        prompts: list[str],
    ) -> None:
        follow_ups = self._follow_up_queues[session_id]
        follow_ups.appendleft(_QueuedPromptBatch(kind="next", prompts=list(prompts)))

    def _append_follow_up_locked(
        self,
        session_id: str,
        *,
        prompt: str,
        kind: str,
    ) -> int:
        follow_ups = self._follow_up_queues[session_id]
        if follow_ups and follow_ups[-1].kind == kind:
            follow_ups[-1].prompts.append(prompt)
            return len(follow_ups[-1].prompts)
        follow_ups.append(_QueuedPromptBatch(kind=kind, prompts=[prompt]))
        return 1

    def _build_queue_state_event_locked(
        self,
        session_id: str,
    ) -> SessionQueueStateEvent:
        next_prompts: list[str] = []
        queue = self._steer_queues.get(session_id)
        if queue:
            next_prompts.extend(queue)

        later_prompts: list[str] = []
        for batch in self._follow_up_queues.get(session_id, ()):
            if batch.kind == "later":
                later_prompts.extend(batch.prompts)

        return SessionQueueStateEvent(
            next_prompts=next_prompts,
            later_prompts=later_prompts,
        )


@dataclass
class _OpenAICodexLoginFlowState:
    flow: OpenAICodexLoginFlow
    task: asyncio.Task[Any] | None = None
    result: asyncio.Future[Any] | None = None
    started_at: float | None = None


@dataclass
class _PendingApprovalState:
    request_id: str
    request: ApprovalRequest
    response_future: asyncio.Future[ApprovalDecision]


@dataclass
class _PendingOnboardingQuestionState:
    attempt_id: str
    question: OnboardingQuestionRequest
    response_future: asyncio.Future[OnboardingAnswerResult]


@dataclass
class _SessionPermissionContext:
    permission_state: PermissionState
    permission_memory: SessionPermissionMemory


@dataclass
class _RpcRuntimeState:
    follow_up_state: _FollowUpState = field(default_factory=_FollowUpState)
    openai_codex_login_flows: dict[str, _OpenAICodexLoginFlowState] = field(
        default_factory=dict
    )
    permission_states: dict[str, _SessionPermissionContext] = field(
        default_factory=dict
    )
    pending_approvals: dict[str, dict[str, _PendingApprovalState]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    pending_onboarding_questions: dict[
        str, dict[str, _PendingOnboardingQuestionState]
    ] = field(default_factory=lambda: defaultdict(dict))


def _new_runtime_state() -> _RpcRuntimeState:
    return _RpcRuntimeState()


_RUNTIME_STATE = _new_runtime_state()
_DEFAULT_PERMISSION_STATE_KEY = "__workspace_default__"


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


def _combine_prompt_batch(prompts: list[str]) -> str:
    return "\n\n".join(prompts)


def _build_live_permission_state(
    *,
    sandbox_policy: SandboxPolicy | None = None,
    approval_policy: ApprovalPolicy | None = None,
) -> PermissionState:
    default_state = build_default_permission_state()
    resolved_sandbox_policy = sandbox_policy or default_state.sandbox_policy
    resolved_approval_policy = approval_policy or default_state.approval_policy
    filesystem_access = default_state.effective_capabilities.filesystem_access
    network_access = default_state.effective_capabilities.network_access
    if isinstance(resolved_sandbox_policy, DangerFullAccessSandboxPolicy):
        filesystem_access = "full_access"
        network_access = "enabled"
    return build_permission_state(
        sandbox_policy=resolved_sandbox_policy,
        approval_policy=resolved_approval_policy,
        effective_capabilities=EffectiveCapabilities(
            filesystem_access=filesystem_access,
            network_access=network_access,
            execution_isolation="unsandboxed",
            approval_mode=resolved_approval_policy.mode,
            approval_by_kind=resolved_approval_policy.by_kind,
        ),
    )


def _permission_state_key(session_id: str | None) -> str:
    if session_id is None:
        return _DEFAULT_PERMISSION_STATE_KEY
    return session_id


def _build_permission_context_for_session(
    session_id: str | None,
) -> _SessionPermissionContext:
    return _SessionPermissionContext(
        permission_state=_build_live_permission_state(),
        permission_memory=SessionPermissionMemory(),
    )


def _get_or_create_permission_context(
    session_id: str | None,
) -> _SessionPermissionContext:
    state = _RUNTIME_STATE.permission_states.get(_permission_state_key(session_id))
    if state is None:
        state = _build_permission_context_for_session(session_id)
        _RUNTIME_STATE.permission_states[_permission_state_key(session_id)] = state
    return state


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


def _workspace_is_trusted(workspace_root: Path | str) -> bool:
    return workspace_trust_status(workspace_root).trusted


def _workspace_project_docs_root(workspace_root: Path | str) -> Path:
    return resolve_workspace_trust_target(workspace_root)


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
    _RUNTIME_STATE.permission_states[session_id] = _SessionPermissionContext(
        permission_state=_get_or_create_permission_context(
            None
        ).permission_state.model_copy(deep=True),
        permission_memory=SessionPermissionMemory(),
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


def _workspace_untrusted_error(
    *,
    request_id: str,
    workspace_root: Path | str,
) -> RpcErrorEnvelope:
    status = workspace_trust_status(workspace_root)
    return RpcErrorEnvelope(
        id=request_id,
        error_type="WorkspaceUntrusted",
        message=(
            "Workspace is not trusted yet. Accept trust for "
            f"{status.trust_target} before loading project instructions or "
            "starting a session."
        ),
    )


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


async def _handle_model_catalog(
    request: ModelCatalogRequest,
    _ctx: _RpcContext,
) -> AsyncIterator[str]:
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
    for state in _RUNTIME_STATE.openai_codex_login_flows.values():
        _cancel_login_flow_task(state.task)
        _fail_login_result(state.result, "login flow cancelled")
    _RUNTIME_STATE.openai_codex_login_flows.clear()

    result = asyncio.get_running_loop().create_future()
    _RUNTIME_STATE.openai_codex_login_flows[flow_id] = _OpenAICodexLoginFlowState(
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
    state = _RUNTIME_STATE.openai_codex_login_flows.get(request.payload.flow_id)
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
    state = _RUNTIME_STATE.openai_codex_login_flows.get(request.payload.flow_id)
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
        queued_count = await _RUNTIME_STATE.follow_up_state.enqueue(
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
        promoted_count = await _RUNTIME_STATE.follow_up_state.interrupt(
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
            generated_question = await asyncio.to_thread(
                onboarding_domain.generate_onboarding_mcq,
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
        result = await asyncio.to_thread(
            start_onboarding_mcq,
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
    _RUNTIME_STATE.permission_states.pop(session_id, None)


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

    result = await asyncio.to_thread(
        submit_onboarding_mcq,
        sessions_root=ctx.sessions_root,
        workspace_root=ctx.workspace_root,
        session_id=request.payload.session_id,
        attempt_id=request.payload.attempt_id,
        selected_index=request.payload.selected_index,
    )
    pending = _RUNTIME_STATE.pending_onboarding_questions.get(
        request.payload.session_id,
        {}
    )
    question_state = pending.get(request.payload.attempt_id)
    if question_state is not None and not question_state.response_future.done():
        question_state.response_future.set_result(result)
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
    _RUNTIME_STATE.permission_states[state_key] = _SessionPermissionContext(
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

    pending = _RUNTIME_STATE.pending_approvals.get(request.payload.session_id, {})
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


async def _handle_workspace_project_docs(
    request: WorkspaceProjectDocsRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if not _workspace_is_trusted(ctx.workspace_root):
        yield _workspace_untrusted_error(
            request_id=request.id,
            workspace_root=ctx.workspace_root,
        ).model_dump_json()
        return
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceProjectDocsResponse(
            documents=[
                WorkspaceProjectDoc(
                    path=str(doc.path),
                    filename=doc.filename,
                    truncated=doc.truncated,
                )
                for doc in load_workspace_project_docs(
                    _workspace_project_docs_root(ctx.workspace_root)
                )
            ]
        ),
    ).model_dump_json()


async def _handle_workspace_trust_status(
    request: WorkspaceTrustStatusRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = workspace_trust_status(ctx.workspace_root)
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceTrustStatusResponse(
            trusted=status.trusted,
            trust_target=status.trust_target,
        ),
    ).model_dump_json()


async def _handle_workspace_trust_accept(
    request: WorkspaceTrustAcceptRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = accept_workspace_trust(ctx.workspace_root)
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceTrustAcceptResponse(
            trusted=status.trusted,
            trust_target=status.trust_target,
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
        await _RUNTIME_STATE.follow_up_state.activate_steer_boundary(
            request.payload.session_id,
            attach,
        )

    async def submit_boundary() -> None:
        await _RUNTIME_STATE.follow_up_state.submit_active_steer_boundary(
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
        session_pending = _RUNTIME_STATE.pending_approvals[
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
            current_pending = _RUNTIME_STATE.pending_approvals.get(
                request.payload.session_id
            )
            if current_pending is not None:
                current_pending.pop(decision_request.request_id, None)
                if not current_pending:
                    _RUNTIME_STATE.pending_approvals.pop(
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
        session_pending = _RUNTIME_STATE.pending_onboarding_questions[
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
            await asyncio.to_thread(
                abandon_pending_onboarding_attempt,
                sessions_root=ctx.sessions_root,
                workspace_root=ctx.workspace_root,
                session_id=request.payload.session_id,
                attempt_id=question_request.attempt_id,
            )
            raise
        finally:
            current_pending = _RUNTIME_STATE.pending_onboarding_questions.get(
                request.payload.session_id
            )
            if current_pending is not None:
                current_pending.pop(question_request.attempt_id, None)
                if not current_pending:
                    _RUNTIME_STATE.pending_onboarding_questions.pop(
                        request.payload.session_id, None
                    )

    await _RUNTIME_STATE.follow_up_state.activate(
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
                    tool_names=CANONICAL_TOOL_NAMES,
                    thinking=request.payload.thinking,
                    permission_state=_get_or_create_permission_context(
                        request.payload.session_id
                    ).permission_state,
                    permission_memory=_get_or_create_permission_context(
                        request.payload.session_id
                    ).permission_memory,
                    resolve_approval_request=resolve_approval_request,
                    resolve_onboarding_question=resolve_onboarding_question,
                    activate_steer_boundary=activate_boundary,
                    submit_steer_boundary=submit_boundary,
                    deactivate_steer_boundary=lambda: (
                        _RUNTIME_STATE.follow_up_state.deactivate_steer_boundary(
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

            follow_up_state = _RUNTIME_STATE.follow_up_state
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
        await _RUNTIME_STATE.follow_up_state.deactivate(request.payload.session_id)
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


def _prune_stale_login_flows(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    stale_ids = [
        flow_id
        for flow_id, state in _RUNTIME_STATE.openai_codex_login_flows.items()
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
    return _RUNTIME_STATE.openai_codex_login_flows.pop(flow_id, None)


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


def _extract_request_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        request_id = payload.get("id")
        if isinstance(request_id, str):
            return request_id

    return None


def _find_oauth_provider_status(provider: str):
    for status in get_oauth_provider_statuses():
        if status.provider == provider:
            return status
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
