from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from pydantic import TypeAdapter, ValidationError

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
)
from just_another_coding_agent.contracts.run_events import (
    RunEvent,
    SessionLifecycleEvent,
    SessionQueuedPromptBatchSubmittedEvent,
    SessionQueueStateEvent,
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
from just_another_coding_agent.runtime.observability import logfire_setup_status
from just_another_coding_agent.runtime.project_docs import (
    load_workspace_project_docs,
)
from just_another_coding_agent.runtime.session import stream_session_run_events
from just_another_coding_agent.session import (
    SessionFormatError,
    SessionNameValidationError,
    append_session_name_to_session,
    build_session_preview,
)

_RPC_REQUEST_ADAPTER = TypeAdapter(RpcRequest)
_LOGIN_FLOW_TTL_SECONDS = 15 * 60
_OPENAI_CODEX_LOGIN_FLOWS: dict[str, OpenAICodexLoginFlow] = {}
_OPENAI_CODEX_LOGIN_TASKS: dict[str, asyncio.Task] = {}
_OPENAI_CODEX_LOGIN_RESULTS: dict[str, asyncio.Future] = {}
_OPENAI_CODEX_LOGIN_STARTED_AT: dict[str, float] = {}


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


_FOLLOW_UP_STATE = _FollowUpState()


def _combine_prompt_batch(prompts: list[str]) -> str:
    return "\n\n".join(prompts)


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

    if isinstance(request, SessionCreateRequest):
        session_id = create_session(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
        )
        yield RpcResponseEnvelope(
            id=request.id,
            response=SessionCreateResponse(
                session_id=session_id,
                project_docs=[
                    WorkspaceProjectDoc(
                        path=str(doc.path),
                        filename=doc.filename,
                        truncated=doc.truncated,
                    )
                    for doc in load_workspace_project_docs(workspace_root)
                ],
            ),
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
                oauth_providers=get_oauth_provider_statuses(),
            ),
        ).model_dump_json()
        return

    if isinstance(request, TraceLogfireStatusRequest):
        status = logfire_setup_status()
        yield RpcResponseEnvelope(
            id=request.id,
            response=TraceLogfireStatusResponse(
                installed=status.installed,
                credentials_configured=status.credentials_configured,
            ),
        ).model_dump_json()
        return

    if isinstance(request, AuthPrepareFileRequest):
        try:
            prepared = prepare_provider_secret_file(request.payload.provider)
        except AuthStoreError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return

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

    if isinstance(request, AuthLoginOpenAICodexStartRequest):
        try:
            flow, flow_id, auth_url, instructions = start_openai_codex_oauth_login()
        except AuthStoreError as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return
        _reset_login_flow_group(
            flows=_OPENAI_CODEX_LOGIN_FLOWS,
            tasks=_OPENAI_CODEX_LOGIN_TASKS,
            results=_OPENAI_CODEX_LOGIN_RESULTS,
            started_at=_OPENAI_CODEX_LOGIN_STARTED_AT,
        )
        result = asyncio.get_running_loop().create_future()
        _OPENAI_CODEX_LOGIN_FLOWS[flow_id] = flow
        _OPENAI_CODEX_LOGIN_TASKS[flow_id] = asyncio.create_task(
            _drive_login_result(
                result=result,
                wait_for_status=wait_for_openai_codex_oauth_login(flow),
            )
        )
        _OPENAI_CODEX_LOGIN_RESULTS[flow_id] = result
        _OPENAI_CODEX_LOGIN_STARTED_AT[flow_id] = time.monotonic()
        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthLoginOpenAICodexStartResponse(
                flow_id=flow_id,
                auth_url=auth_url,
                instructions=instructions,
            ),
        ).model_dump_json()
        return

    if isinstance(request, AuthLoginOpenAICodexWaitRequest):
        result = _OPENAI_CODEX_LOGIN_RESULTS.get(request.payload.flow_id)
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
            _OPENAI_CODEX_LOGIN_TASKS.pop(request.payload.flow_id, None)
            _OPENAI_CODEX_LOGIN_FLOWS.pop(request.payload.flow_id, None)
            _OPENAI_CODEX_LOGIN_RESULTS.pop(request.payload.flow_id, None)
            _OPENAI_CODEX_LOGIN_STARTED_AT.pop(request.payload.flow_id, None)
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return
        _OPENAI_CODEX_LOGIN_TASKS.pop(request.payload.flow_id, None)
        _OPENAI_CODEX_LOGIN_FLOWS.pop(request.payload.flow_id, None)
        _OPENAI_CODEX_LOGIN_RESULTS.pop(request.payload.flow_id, None)
        _OPENAI_CODEX_LOGIN_STARTED_AT.pop(request.payload.flow_id, None)
        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthLoginOpenAICodexWaitResponse(status=status),
        ).model_dump_json()
        return

    if isinstance(request, AuthLoginOpenAICodexCompleteRequest):
        flow = _OPENAI_CODEX_LOGIN_FLOWS.get(request.payload.flow_id)
        result = _OPENAI_CODEX_LOGIN_RESULTS.get(request.payload.flow_id)
        if flow is None or result is None:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InvalidRequest",
                message="unknown OpenAI Codex login flow",
            ).model_dump_json()
            return
        try:
            status = await complete_openai_codex_oauth_login(
                flow,
                request.payload.callback_or_code,
            )
        except Exception as error:
            yield RpcErrorEnvelope(
                id=request.id,
                error_type="InternalError",
                message=str(error),
            ).model_dump_json()
            return
        task = _OPENAI_CODEX_LOGIN_TASKS.pop(request.payload.flow_id, None)
        if not result.done():
            result.set_result(status)
        _cancel_login_flow_task(task)
        _OPENAI_CODEX_LOGIN_FLOWS.pop(request.payload.flow_id, None)
        _OPENAI_CODEX_LOGIN_RESULTS.pop(request.payload.flow_id, None)
        _OPENAI_CODEX_LOGIN_STARTED_AT.pop(request.payload.flow_id, None)
        yield RpcResponseEnvelope(
            id=request.id,
            response=AuthLoginOpenAICodexCompleteResponse(status=status),
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
        return
    if isinstance(request, RunInterruptRequest):
        try:
            promoted_count = await _FOLLOW_UP_STATE.interrupt(
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

    if isinstance(request, WorkspaceProjectDocsRequest):
        yield RpcResponseEnvelope(
            id=request.id,
            response=WorkspaceProjectDocsResponse(
                documents=[
                    WorkspaceProjectDoc(
                        path=str(doc.path),
                        filename=doc.filename,
                        truncated=doc.truncated,
                    )
                    for doc in load_workspace_project_docs(workspace_root)
                ]
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
    current_task = asyncio.current_task()
    if current_task is None:
        raise RuntimeError("run.start must execute inside an asyncio task")
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
        await _FOLLOW_UP_STATE.activate_steer_boundary(
            request.payload.session_id,
            attach,
        )

    async def submit_boundary() -> None:
        await _FOLLOW_UP_STATE.submit_active_steer_boundary(request.payload.session_id)

    await _FOLLOW_UP_STATE.activate(
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
                    model=model,
                    workspace_root=workspace_root,
                    session_path=session_path,
                    prompt=prompt,
                    tool_names=CANONICAL_TOOL_NAMES,
                    thinking=request.payload.thinking,
                    activate_steer_boundary=activate_boundary,
                    submit_steer_boundary=submit_boundary,
                    deactivate_steer_boundary=lambda: (
                        _FOLLOW_UP_STATE.deactivate_steer_boundary(
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

            await _FOLLOW_UP_STATE.downgrade_pending_steers_to_follow_ups(
                request.payload.session_id
            )
            prompt_batch = await _FOLLOW_UP_STATE.take_next_follow_up_batch(
                request.payload.session_id
            )
            if prompt_batch is None:
                break
            await emit_submitted_prompt_batch("later", prompt_batch)
            prompt = _combine_prompt_batch(prompt_batch)
    finally:
        await _FOLLOW_UP_STATE.deactivate(request.payload.session_id)
    yield RpcResponseEnvelope(
        id=request.id,
        response=RunStartResponse(session_id=request.payload.session_id),
    ).model_dump_json()


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
                    await asyncio.to_thread(_sync_drain, batch)
                    return
                batch.append(nxt)
            try:
                await asyncio.to_thread(_sync_drain, batch)
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


def _prune_stale_login_flows(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    _prune_login_flow_group(
        flows=_OPENAI_CODEX_LOGIN_FLOWS,
        tasks=_OPENAI_CODEX_LOGIN_TASKS,
        results=_OPENAI_CODEX_LOGIN_RESULTS,
        started_at=_OPENAI_CODEX_LOGIN_STARTED_AT,
        now=current,
        error_message="OpenAI Codex login flow expired",
    )


def _prune_login_flow_group(
    *,
    flows: dict[str, Any],
    tasks: dict[str, asyncio.Task],
    results: dict[str, asyncio.Future],
    started_at: dict[str, float],
    now: float,
    error_message: str,
) -> None:
    stale_flow_ids: list[str] = []
    for flow_id, started in started_at.items():
        task = tasks.get(flow_id)
        expired = (now - started) > _LOGIN_FLOW_TTL_SECONDS
        if expired:
            _cancel_login_flow_task(task)
            _fail_login_result(results.get(flow_id), error_message)
            stale_flow_ids.append(flow_id)
    for flow_id in stale_flow_ids:
        flows.pop(flow_id, None)
        tasks.pop(flow_id, None)
        results.pop(flow_id, None)
        started_at.pop(flow_id, None)


def _reset_login_flow_group(
    *,
    flows: dict[str, Any],
    tasks: dict[str, asyncio.Task],
    results: dict[str, asyncio.Future],
    started_at: dict[str, float],
) -> None:
    for task in tasks.values():
        _cancel_login_flow_task(task)
    for result in results.values():
        _fail_login_result(result, "login flow cancelled")
    flows.clear()
    tasks.clear()
    results.clear()
    started_at.clear()


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
    }:
        return None
    request_payload = payload.get("payload")
    if not isinstance(request_payload, dict):
        return None
    session_id = request_payload.get("session_id")
    if isinstance(session_id, str):
        return session_id
    return None
