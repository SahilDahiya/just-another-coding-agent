from __future__ import annotations

import pytest

from just_another_coding_agent.contracts.mcp import (
    JACA_ONBOARDING_MCP_SERVER_ID,
    McpToolIdentity,
)
from just_another_coding_agent.runtime import (
    build_default_mcp_manager as lazy_build_default_mcp_manager,
)
from just_another_coding_agent.runtime.mcp import (
    DEFAULT_BUILTIN_MCP_SERVERS,
    McpManager,
    McpServerDefinition,
    McpToolDefinition,
    UnknownMcpServerError,
    UnknownMcpToolError,
    build_default_mcp_manager,
)


def test_default_mcp_manager_discovers_builtin_onboarding_server() -> None:
    manager = build_default_mcp_manager()

    servers = manager.list_servers()

    assert [server.server_id for server in servers] == [JACA_ONBOARDING_MCP_SERVER_ID]
    assert servers == DEFAULT_BUILTIN_MCP_SERVERS
    assert lazy_build_default_mcp_manager().list_servers() == servers


def test_default_mcp_manager_discovers_onboarding_tool_metadata() -> None:
    manager = build_default_mcp_manager()

    tools = manager.discover_tools(server_id=JACA_ONBOARDING_MCP_SERVER_ID)

    assert [tool.model_tool_name for tool in tools] == [
        "mcp__jaca_onboarding__ask_mcq_question",
        "mcp__jaca_onboarding__generate_mcq_from_teaching_packets",
        "mcp__jaca_onboarding__publish_teaching_packet",
    ]
    assert all(
        tool.identity.server_id == JACA_ONBOARDING_MCP_SERVER_ID for tool in tools
    )
    assert all(tool.sequential for tool in tools)
    assert all(tool.description for tool in tools)


def test_mcp_manager_resolves_tool_by_model_facing_name() -> None:
    manager = build_default_mcp_manager()

    tool = manager.get_tool("mcp__jaca_onboarding__publish_teaching_packet")

    assert tool.identity == McpToolIdentity(
        server_id="jaca_onboarding",
        tool_name="publish_teaching_packet",
    )
    assert tool.model_tool_name == "mcp__jaca_onboarding__publish_teaching_packet"


def test_mcp_manager_fails_hard_for_unknown_server_or_tool() -> None:
    manager = build_default_mcp_manager()

    with pytest.raises(UnknownMcpServerError, match="missing_server"):
        manager.discover_tools(server_id="missing_server")

    with pytest.raises(UnknownMcpToolError, match="missing_tool"):
        manager.get_tool("mcp__jaca_onboarding__missing_tool")

    with pytest.raises(ValueError, match="mcp__"):
        manager.get_tool("not_mcp")


def test_mcp_manager_rejects_duplicate_servers_and_tools() -> None:
    server = McpServerDefinition(
        server_id="demo",
        display_name="Demo",
        tools=(
            McpToolDefinition(
                identity=McpToolIdentity(server_id="demo", tool_name="echo"),
                title="Echo",
                description="Echo input.",
            ),
        ),
    )

    with pytest.raises(ValueError, match="Duplicate MCP server id"):
        McpManager((server, server))

    with pytest.raises(ValueError, match="Duplicate MCP tool"):
        McpServerDefinition(
            server_id="demo",
            display_name="Demo",
            tools=(server.tools[0], server.tools[0]),
        )


def test_mcp_server_definition_rejects_mismatched_tool_server_id() -> None:
    with pytest.raises(ValueError, match="does not belong to MCP server"):
        McpServerDefinition(
            server_id="demo",
            display_name="Demo",
            tools=(
                McpToolDefinition(
                    identity=McpToolIdentity(server_id="other", tool_name="echo"),
                    title="Echo",
                    description="Echo input.",
                ),
            ),
        )


def test_mcp_runtime_definitions_require_human_readable_text() -> None:
    with pytest.raises(ValueError, match="title"):
        McpToolDefinition(
            identity=McpToolIdentity(server_id="demo", tool_name="echo"),
            title="",
            description="Echo input.",
        )

    with pytest.raises(ValueError, match="description"):
        McpToolDefinition(
            identity=McpToolIdentity(server_id="demo", tool_name="echo"),
            title="Echo",
            description="",
        )

    with pytest.raises(ValueError, match="display_name"):
        McpServerDefinition(server_id="demo", display_name="")
