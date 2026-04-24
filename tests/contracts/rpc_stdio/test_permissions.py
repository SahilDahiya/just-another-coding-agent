import asyncio
import json

from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalPolicy,
    CommandExecutionApprovalRequest,
    EffectiveCapabilities,
    PermissionState,
    WorkspaceWriteSandboxPolicy,
)
from just_another_coding_agent.rpc.stdio import handle_rpc_json_line
from tests.contracts.rpc_stdio_test_support import (
    create_session_id,
    noop_emit_rpc_event,
    rpc_messages,
    text_only_stream,
)


async def test_handle_rpc_json_line_returns_live_permission_state(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-permission-get",
            "command": "permission.get",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-get",
            "response": {
                "session_id": session_id,
                "permission_state": PermissionState(
                    sandbox_policy=WorkspaceWriteSandboxPolicy(),
                    approval_policy=ApprovalPolicy(mode="on_escalation"),
                    effective_capabilities=EffectiveCapabilities(
                        filesystem_access="workspace_write",
                        network_access="restricted",
                        execution_isolation="unsandboxed",
                        approval_mode="on_escalation",
                    ),
                ).model_dump(mode="json"),
            },
        }
    ]


async def test_handle_rpc_json_line_returns_workspace_default_permission_state(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await rpc_messages(
        request_payload={
            "id": "req-permission-get",
            "command": "permission.get",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-get",
            "response": {
                "session_id": None,
                "permission_state": PermissionState(
                    sandbox_policy=WorkspaceWriteSandboxPolicy(),
                    approval_policy=ApprovalPolicy(mode="on_escalation"),
                    effective_capabilities=EffectiveCapabilities(
                        filesystem_access="workspace_write",
                        network_access="restricted",
                        execution_isolation="unsandboxed",
                        approval_mode="on_escalation",
                    ),
                ).model_dump(mode="json"),
            },
        }
    ]


async def test_handle_rpc_json_line_sets_live_permission_state_for_session(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    set_messages = await rpc_messages(
        request_payload={
            "id": "req-permission-set",
            "command": "permission.set",
            "payload": {
                "session_id": session_id,
                "sandbox_policy": {"mode": "workspace_write"},
                "approval_policy": {"mode": "always"},
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    expected_state = PermissionState(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="always"),
        effective_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="restricted",
            execution_isolation="unsandboxed",
            approval_mode="always",
        ),
    )
    assert set_messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-set",
            "response": {
                "session_id": session_id,
                "permission_state": expected_state.model_dump(mode="json"),
            },
        }
    ]

    get_messages = await rpc_messages(
        request_payload={
            "id": "req-permission-get",
            "command": "permission.get",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert get_messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-get",
            "response": {
                "session_id": session_id,
                "permission_state": expected_state.model_dump(mode="json"),
            },
        }
    ]


async def test_handle_rpc_json_line_sets_workspace_default_permission_state(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    set_messages = await rpc_messages(
        request_payload={
            "id": "req-permission-set",
            "command": "permission.set",
            "payload": {
                "approval_policy": {"mode": "always"},
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    expected_state = PermissionState(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="always"),
        effective_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="restricted",
            execution_isolation="unsandboxed",
            approval_mode="always",
        ),
    )
    assert set_messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-set",
            "response": {
                "session_id": None,
                "permission_state": expected_state.model_dump(mode="json"),
            },
        }
    ]

    created_session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    get_messages = await rpc_messages(
        request_payload={
            "id": "req-permission-get",
            "command": "permission.get",
            "payload": {"session_id": created_session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert get_messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-get",
            "response": {
                "session_id": created_session_id,
                "permission_state": expected_state.model_dump(mode="json"),
            },
        }
    ]


async def test_handle_rpc_json_line_sets_request_kind_approval_override(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    messages = await rpc_messages(
        request_payload={
            "id": "req-permission-set",
            "command": "permission.set",
            "payload": {
                "approval_policy": {
                    "mode": "on_escalation",
                    "by_kind": {"file_change": "always"},
                },
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    expected_state = PermissionState(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(
            mode="on_escalation",
            by_kind={"file_change": "always"},
        ),
        effective_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="restricted",
            execution_isolation="unsandboxed",
            approval_mode="on_escalation",
            approval_by_kind={"file_change": "always"},
        ),
    )
    assert messages == [
        {
            "type": "rpc_response",
            "id": "req-permission-set",
            "response": {
                "session_id": None,
                "permission_state": expected_state.model_dump(mode="json"),
            },
        }
    ]


async def test_handle_rpc_json_line_rejects_unknown_approval_request(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-approval-submit",
            "command": "approval.submit",
            "payload": {
                "session_id": session_id,
                "decision": {
                    "request_id": "approval-1",
                    "decision": "approved",
                },
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-approval-submit",
            "error_type": "InvalidRequest",
            "message": "Unknown approval request for session: approval-1",
        }
    ]


async def test_handle_rpc_json_line_resolves_pending_approval_submit(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    approval_requested = asyncio.Event()
    captured: dict[str, ApprovalDecision] = {}

    async def fake_stream_session_run_events(
        *,
        resolve_approval_request=None,
        **_kwargs,
    ):
        assert resolve_approval_request is not None
        yield {"type": "run_started", "run_id": "run-1"}
        request = CommandExecutionApprovalRequest(
            request_id="approval-1",
            request_kind="command_execution",
            reason="let the tool continue",
            command="curl https://example.com",
            cwd=str(workspace_root.resolve()),
            shell_family="posix",
            requested_capabilities=EffectiveCapabilities(
                filesystem_access="full_access",
                network_access="enabled",
                execution_isolation="unsandboxed",
                approval_mode="never",
            ),
            requested_permissions=AdditionalSandboxPermissions(
                network_access="enabled",
            ),
            requested_grants=(
                {
                    "permissions": {
                        "network_access": "enabled",
                    },
                    "scope": "once",
                },
            ),
        )
        yield {
            "type": "approval_requested",
            "run_id": "run-1",
            "request": request.model_dump(mode="json"),
        }
        approval_requested.set()
        decision = await resolve_approval_request(request)
        captured["decision"] = decision
        yield {
            "type": "approval_resolved",
            "run_id": "run-1",
            "decision": decision.model_dump(mode="json"),
        }
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    async def collect_run_messages() -> list[dict[str, object]]:
        return [
            json.loads(line)
            async for line in handle_rpc_json_line(
                line=json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": session_id,
                            "prompt": "go",
                        },
                    }
                ),
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                sessions_root=sessions_root,
                emit_rpc_event=noop_emit_rpc_event,
            )
        ]

    run_task = asyncio.create_task(collect_run_messages())
    await approval_requested.wait()

    submit_messages = await rpc_messages(
        request_payload={
            "id": "req-approval-submit",
            "command": "approval.submit",
            "payload": {
                "session_id": session_id,
                "decision": {
                    "request_id": "approval-1",
                    "decision": "approved",
                },
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    run_messages = await run_task

    assert submit_messages == [
        {
            "type": "rpc_response",
            "id": "req-approval-submit",
            "response": {
                "session_id": session_id,
                    "decision": {
                        "request_id": "approval-1",
                        "decision": "approved",
                        "option_id": None,
                        "granted_permissions": {
                            "network_access": "enabled",
                            "extra_read_roots": [],
                            "extra_write_roots": [],
                    },
                    "granted_grants": [
                        {
                                "permissions": {
                                    "network_access": "enabled",
                                    "extra_read_roots": [],
                                    "extra_write_roots": [],
                                },
                                "scope": "once",
                                "command_prefix": [],
                            }
                        ],
                    },
                },
        }
    ]
    assert captured["decision"] == ApprovalDecision(
        request_id="approval-1",
        decision="approved",
        granted_permissions=AdditionalSandboxPermissions(
            network_access="enabled",
        ),
        granted_grants=(
            {
                "permissions": {
                    "network_access": "enabled",
                },
                "scope": "once",
            },
        ),
    )
    assert [message["type"] for message in run_messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
    assert [message["event"]["type"] for message in run_messages[:-1]] == [
        "run_started",
        "approval_requested",
        "approval_resolved",
        "run_succeeded",
    ]


async def test_handle_rpc_json_line_forwards_live_permission_state_to_session_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    captured: dict[str, object] = {}

    await rpc_messages(
        request_payload={
            "id": "req-permission-set",
            "command": "permission.set",
            "payload": {
                "session_id": session_id,
                "sandbox_policy": {"mode": "workspace_write"},
                "approval_policy": {"mode": "always"},
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    async def fake_stream_session_run_events(
        *,
        model,
        workspace_root,
        session_path,
        prompt,
        tool_names,
        thinking=None,
        permission_state=None,
        **_kwargs,
    ):
        captured["thinking"] = thinking
        captured["prompt"] = prompt
        captured["permission_state"] = permission_state
        yield {"type": "run_started", "run_id": "run-1"}
        yield {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"}

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "go",
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert captured == {
        "thinking": None,
        "prompt": "go",
        "permission_state": PermissionState(
            sandbox_policy=WorkspaceWriteSandboxPolicy(),
            approval_policy=ApprovalPolicy(mode="always"),
            effective_capabilities=EffectiveCapabilities(
                filesystem_access="workspace_write",
                network_access="restricted",
                execution_isolation="unsandboxed",
                approval_mode="always",
            ),
        ),
    }
    assert [message["type"] for message in messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
