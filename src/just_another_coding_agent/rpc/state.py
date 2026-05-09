from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Callable

from just_another_coding_agent.auth import OpenAICodexLoginFlow
from just_another_coding_agent.contracts.onboarding import (
    OnboardingAnswerResult,
    OnboardingQuestionRequest,
)
from just_another_coding_agent.contracts.run_events import (
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
)
from just_another_coding_agent.tools.deps import SessionPermissionMemory


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
