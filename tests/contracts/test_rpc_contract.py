from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from just_another_coding_agent.contracts.rpc import (
    RpcEventEnvelope,
    RpcRequest,
    RpcResponseEnvelope,
)
from just_another_coding_agent.contracts.sandbox import (
    AdditionalSandboxPermissions,
    ApprovalDecision,
    ApprovalPolicy,
    DangerFullAccessSandboxPolicy,
    PermissionState,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)


def test_rpc_request_accepts_permission_get_and_set_commands() -> None:
    adapter = TypeAdapter(RpcRequest)

    permission_get = adapter.validate_python(
        {
            "id": "req-get",
            "command": "permission.get",
            "payload": {"session_id": "a" * 32},
        }
    )
    assert permission_get.command == "permission.get"

    permission_set = adapter.validate_python(
        {
            "id": "req-set",
            "command": "permission.set",
            "payload": {
                "session_id": "a" * 32,
                "sandbox_policy": {
                    "mode": "workspace_write",
                    "network_access": "restricted",
                },
                "approval_policy": {"mode": "on_escalation"},
            },
        }
    )
    assert permission_set.command == "permission.set"

    permission_get_without_session = adapter.validate_python(
        {
            "id": "req-get-default",
            "command": "permission.get",
            "payload": {},
        }
    )
    assert permission_get_without_session.payload.session_id is None

    permission_set_without_session = adapter.validate_python(
        {
            "id": "req-set-default",
            "command": "permission.set",
            "payload": {
                "approval_policy": {"mode": "always"},
            },
        }
    )
    assert permission_set_without_session.payload.session_id is None


def test_rpc_request_rejects_empty_permission_set_payload() -> None:
    adapter = TypeAdapter(RpcRequest)

    with pytest.raises(ValidationError, match="at least one explicit policy"):
        adapter.validate_python(
            {
                "id": "req-set",
                "command": "permission.set",
                "payload": {"session_id": "a" * 32},
            }
        )


def test_rpc_request_accepts_approval_submit_command() -> None:
    adapter = TypeAdapter(RpcRequest)

    approval_submit = adapter.validate_python(
        {
            "id": "req-approve",
            "command": "approval.submit",
            "payload": {
                "session_id": "a" * 32,
                "decision": {
                    "request_id": "approval-1",
                    "decision": "approved",
                },
            },
        }
    )

    assert approval_submit.command == "approval.submit"


def test_rpc_event_envelope_accepts_approval_events() -> None:
    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )

    requested_event = RpcEventEnvelope.model_validate(
        {
            "type": "rpc_event",
            "id": "req-run",
            "event": {
                "type": "approval_requested",
                "run_id": "run-1",
                "request": {
                    "request_id": "approval-1",
                    "request_kind": "command_execution",
                    "reason": "Enable network for a package install.",
                    "command": "curl https://example.com",
                    "cwd": "/workspace",
                    "shell_family": "posix",
                    "requested_capabilities": {
                        "filesystem_access": "workspace_write",
                        "network_access": "enabled",
                        "execution_isolation": "sandboxed",
                        "approval_mode": "on_escalation",
                    },
                    "requested_permissions": {
                        "network_access": "enabled",
                        "extra_read_roots": [],
                        "extra_write_roots": [],
                    },
                    "requested_grants": [
                        {
                            "permissions": {
                                "network_access": "enabled",
                                "extra_read_roots": [],
                                "extra_write_roots": [],
                            },
                            "scope": "once",
                        }
                    ],
                },
                "tool_name": "shell",
                "tool_call_id": "call-1",
            },
        }
    )

    assert requested_event.event.type == "approval_requested"
    assert requested_event.event.request.request_kind == "command_execution"

    resolved_event = RpcEventEnvelope.model_validate(
        {
            "type": "rpc_event",
            "id": "req-run",
            "event": {
                "type": "approval_resolved",
                "run_id": "run-1",
                "decision": {
                    "request_id": "approval-1",
                    "decision": "approved",
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
                        }
                    ],
                },
            },
        }
    )

    assert resolved_event.event.type == "approval_resolved"
    assert resolved_event.event.decision == ApprovalDecision(
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
    assert permission_state.effective_capabilities.approval_mode == "on_escalation"


def test_rpc_response_envelope_accepts_permission_and_approval_payloads() -> None:
    permission_state = PermissionState(
        sandbox_policy=DangerFullAccessSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="never"),
        effective_capabilities=build_permission_state(
            sandbox_policy=DangerFullAccessSandboxPolicy(),
            approval_policy=ApprovalPolicy(mode="never"),
        ).effective_capabilities,
    )

    permission_response = RpcResponseEnvelope.model_validate(
        {
            "type": "rpc_response",
            "id": "req-set",
            "response": {
                "session_id": "a" * 32,
                "permission_state": permission_state.model_dump(mode="json"),
            },
        }
    )
    assert permission_response.response.session_id == "a" * 32

    default_permission_response = RpcResponseEnvelope.model_validate(
        {
            "type": "rpc_response",
            "id": "req-default",
            "response": {
                "session_id": None,
                "permission_state": permission_state.model_dump(mode="json"),
            },
        }
    )
    assert default_permission_response.response.session_id is None

    approval_response = RpcResponseEnvelope.model_validate(
        {
            "type": "rpc_response",
            "id": "req-approve",
            "response": {
                "session_id": "a" * 32,
                "decision": {
                    "request_id": "approval-1",
                    "decision": "approved",
                },
            },
        }
    )
    assert approval_response.response.decision.decision == "approved"
