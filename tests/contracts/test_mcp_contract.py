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
        "McpToolCallProvenance",
        "McpToolIdentity",
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
    }

    with pytest.raises(ValidationError):
        McpActivityDetails(
            server_id="jaca_onboarding",
            tool_name="ask_mcq_question",
            model_tool_name="mcp__jaca_onboarding__publish_teaching_packet",
            provenance=mcp.McpToolCallProvenance(source="top_level_model"),
        )
