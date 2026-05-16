from __future__ import annotations

import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts import mcp
from just_another_coding_agent.contracts.run_events import (
    McpActivityDetails,
    ToolActivity,
)


def test_mcp_contract_exports_expected_types() -> None:
    assert set(mcp.__all__) == {
        "JACA_ONBOARDING_MCP_SERVER_ID",
        "JACA_ONBOARDING_MCP_TOOL_NAMES",
        "MCP_TOOL_NAME_PREFIX",
        "McpCallSource",
        "McpFailure",
        "McpFailureKind",
        "McpMountedToolIdentity",
        "McpServerConfig",
        "McpServerToolConfig",
        "McpStdioTransport",
        "McpStreamableHttpTransport",
        "McpToolApprovalMode",
        "McpToolCallProvenance",
        "McpToolIdentity",
        "McpTransport",
        "make_mcp_model_tool_name",
        "parse_mcp_model_tool_name",
    }


def test_mcp_tool_names_are_stable_and_namespaced() -> None:
    model_tool_name = mcp.make_mcp_model_tool_name(
        server_id=mcp.JACA_ONBOARDING_MCP_SERVER_ID,
        tool_name="publish_teaching_packet",
    )

    assert model_tool_name == "mcp__jaca_onboarding__publish_teaching_packet"

    identity = mcp.parse_mcp_model_tool_name(model_tool_name)

    assert identity.server_id == "jaca_onboarding"
    assert identity.tool_name == "publish_teaching_packet"
    assert identity.model_tool_name == model_tool_name


@pytest.mark.parametrize(
    ("server_id", "tool_name"),
    [
        ("JacaOnboarding", "publish_teaching_packet"),
        ("jaca-onboarding", "publish_teaching_packet"),
        ("jaca__onboarding", "publish_teaching_packet"),
        ("jaca_onboarding", "publish-teaching-packet"),
        ("jaca_onboarding", "publish__teaching_packet"),
        ("1jaca_onboarding", "publish_teaching_packet"),
    ],
)
def test_mcp_identity_rejects_names_that_cannot_be_model_tool_names(
    server_id: str,
    tool_name: str,
) -> None:
    with pytest.raises(ValueError):
        mcp.make_mcp_model_tool_name(server_id=server_id, tool_name=tool_name)

    with pytest.raises(ValidationError):
        mcp.McpToolIdentity(server_id=server_id, tool_name=tool_name)


@pytest.mark.parametrize(
    "model_tool_name",
    [
        "jaca_onboarding__publish_teaching_packet",
        "mcp__jaca_onboarding",
        "mcp__jaca_onboarding__",
        "mcp__jaca_onboarding__publish__teaching_packet",
    ],
)
def test_parse_mcp_model_tool_name_fails_hard_for_invalid_names(
    model_tool_name: str,
) -> None:
    with pytest.raises(ValueError):
        mcp.parse_mcp_model_tool_name(model_tool_name)


def test_mcp_top_level_provenance_forbids_nested_code_mode_fields() -> None:
    provenance = mcp.McpToolCallProvenance(source="top_level_model")

    assert provenance.model_dump(mode="json") == {
        "source": "top_level_model",
        "parent_tool_call_id": None,
        "code_mode_cell_id": None,
    }

    with pytest.raises(ValidationError):
        mcp.McpToolCallProvenance(
            source="top_level_model",
            parent_tool_call_id="call-exec",
            code_mode_cell_id="cell-1",
        )


def test_mcp_code_mode_provenance_requires_parent_exec_and_cell() -> None:
    provenance = mcp.McpToolCallProvenance(
        source="code_mode",
        parent_tool_call_id="call-exec",
        code_mode_cell_id="cell-1",
    )

    assert provenance.source == "code_mode"
    assert provenance.parent_tool_call_id == "call-exec"
    assert provenance.code_mode_cell_id == "cell-1"

    with pytest.raises(ValidationError):
        mcp.McpToolCallProvenance(source="code_mode", parent_tool_call_id="call-exec")

    with pytest.raises(ValidationError):
        mcp.McpToolCallProvenance(source="code_mode", code_mode_cell_id="cell-1")


def test_mcp_failure_shape_is_strict_and_typed() -> None:
    failure = mcp.McpFailure(
        kind="tool_failed",
        error_type="McpToolExecutionError",
        message="tool failed",
        server_id="jaca_onboarding",
        tool_name="ask_mcq_question",
    )

    assert failure.model_dump(mode="json") == {
        "kind": "tool_failed",
        "error_type": "McpToolExecutionError",
        "message": "tool failed",
        "server_id": "jaca_onboarding",
        "tool_name": "ask_mcq_question",
        "resource_uri": None,
    }

    with pytest.raises(ValidationError):
        mcp.McpFailure(
            kind="tool_failed",
            error_type="McpToolExecutionError",
            message="tool failed",
            server_id="jaca__onboarding",
        )

    with pytest.raises(ValidationError):
        mcp.McpFailure(
            kind="tool_failed",
            error_type="McpToolExecutionError",
            message="tool failed",
            server_id="jaca_onboarding",
        )

    with pytest.raises(ValidationError):
        mcp.McpFailure(
            kind="resource_failed",
            error_type="McpResourceError",
            message="resource failed",
            server_id="jaca_onboarding",
            tool_name="ask_mcq_question",
            resource_uri="jaca://onboarding/guide",
        )


def test_mcp_startup_and_resource_failures_require_matching_subjects() -> None:
    startup = mcp.McpFailure(
        kind="startup_failed",
        error_type="McpServerStartupError",
        message="server failed",
        server_id="jaca_onboarding",
    )
    resource = mcp.McpFailure(
        kind="resource_failed",
        error_type="McpResourceError",
        message="resource failed",
        server_id="jaca_onboarding",
        resource_uri="jaca://onboarding/guide",
    )

    assert startup.server_id == "jaca_onboarding"
    assert startup.tool_name is None
    assert startup.resource_uri is None
    assert resource.resource_uri == "jaca://onboarding/guide"

    with pytest.raises(ValidationError):
        mcp.McpFailure(
            kind="startup_failed",
            error_type="McpServerStartupError",
            message="server failed",
        )


def test_mcp_activity_details_are_typed_for_tui_rendering() -> None:
    activity = ToolActivity(
        title="MCP jaca_onboarding.ask_mcq_question",
        details=McpActivityDetails(
            server_id="jaca_onboarding",
            tool_name="ask_mcq_question",
            model_tool_name="mcp__jaca_onboarding__ask_mcq_question",
            provenance=mcp.McpToolCallProvenance(source="top_level_model"),
        ),
    )

    assert activity.model_dump(mode="json")["details"] == {
        "kind": "mcp",
        "server_id": "jaca_onboarding",
        "tool_name": "ask_mcq_question",
        "model_tool_name": "mcp__jaca_onboarding__ask_mcq_question",
        "provenance": {
            "source": "top_level_model",
            "parent_tool_call_id": None,
            "code_mode_cell_id": None,
        },
        "failure": None,
        "wrapped_title": None,
        "wrapped_display_label": None,
        "wrapped_summary": None,
        "wrapped_details": None,
    }

    with pytest.raises(ValidationError):
        McpActivityDetails(
            server_id="jaca_onboarding",
            tool_name="ask_mcq_question",
            model_tool_name="mcp__jaca_onboarding__publish_teaching_packet",
            provenance=mcp.McpToolCallProvenance(source="top_level_model"),
        )


def test_mcp_server_config_models_streamable_http_transport() -> None:
    config = mcp.McpServerConfig(
        server_id="linear",
        transport=mcp.McpStreamableHttpTransport(
            url="https://mcp.linear.app/mcp",
            bearer_token_env_var="LINEAR_MCP_TOKEN",
        ),
        required=True,
        startup_timeout_sec=5.0,
        tool_timeout_sec=15.0,
        enabled_tools=["create-issue"],
        disabled_tools=["delete-issue"],
        default_tool_approval="prompt",
        tools={
            "create-issue": mcp.McpServerToolConfig(approval_mode="approve"),
        },
    )

    assert config.model_dump(mode="json", exclude_none=True) == {
        "server_id": "linear",
        "transport": {
            "type": "streamable_http",
            "url": "https://mcp.linear.app/mcp",
            "bearer_token_env_var": "LINEAR_MCP_TOKEN",
        },
        "enabled": True,
        "required": True,
        "startup_timeout_sec": 5.0,
        "tool_timeout_sec": 15.0,
        "enabled_tools": ["create-issue"],
        "disabled_tools": ["delete-issue"],
        "default_tool_approval": "prompt",
        "tools": {
            "create-issue": {
                "approval_mode": "approve",
            },
        },
    }


def test_mcp_server_config_models_stdio_transport() -> None:
    config = mcp.McpServerConfig(
        server_id="memory",
        transport=mcp.McpStdioTransport(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-memory"],
            env={"MEMORY_SCOPE": "session"},
            cwd="/tmp",
        ),
    )

    assert config.transport.type == "stdio"
    assert config.transport.command == "npx"
    assert config.transport.args == ["-y", "@modelcontextprotocol/server-memory"]
    assert config.transport.env == {"MEMORY_SCOPE": "session"}
    assert config.transport.cwd == "/tmp"


def test_mcp_server_config_fails_hard_for_invalid_or_ambiguous_values() -> None:
    with pytest.raises(ValidationError):
        mcp.McpServerConfig(
            server_id="Linear",
            transport=mcp.McpStreamableHttpTransport(url="https://mcp.linear.app/mcp"),
        )

    with pytest.raises(ValidationError):
        mcp.McpStreamableHttpTransport(
            url="https://mcp.linear.app/mcp",
            bearer_token="inline-secret",
        )

    with pytest.raises(ValidationError):
        mcp.McpStreamableHttpTransport(url="file:///tmp/server")

    with pytest.raises(ValidationError):
        mcp.McpServerConfig(
            server_id="linear",
            transport=mcp.McpStreamableHttpTransport(url="https://mcp.linear.app/mcp"),
            enabled_tools=["create-issue"],
            disabled_tools=["create-issue"],
        )

    with pytest.raises(ValidationError):
        mcp.McpServerConfig(
            server_id="linear",
            transport=mcp.McpStreamableHttpTransport(url="https://mcp.linear.app/mcp"),
            startup_timeout_sec=0,
        )


def test_mounted_mcp_tool_identity_preserves_raw_and_model_names() -> None:
    identity = mcp.McpMountedToolIdentity(
        server_id="linear",
        raw_tool_name="create-issue",
        model_tool_name="mcp__linear__create_issue",
    )

    assert identity.raw_tool_name == "create-issue"
    assert identity.model_identity == mcp.McpToolIdentity(
        server_id="linear",
        tool_name="create_issue",
    )

    with pytest.raises(ValidationError):
        mcp.McpMountedToolIdentity(
            server_id="linear",
            raw_tool_name="create-issue",
            model_tool_name="mcp__github__create_issue",
        )
