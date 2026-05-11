from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from just_another_coding_agent.contracts.mcp import (
    JACA_ONBOARDING_MCP_SERVER_ID,
    McpToolIdentity,
    make_mcp_model_tool_name,
    parse_mcp_model_tool_name,
)


class McpManagerError(RuntimeError):
    """Base error for runtime MCP manager failures."""


class UnknownMcpServerError(McpManagerError):
    """Raised when an MCP server id is not mounted in the manager."""


class UnknownMcpToolError(McpManagerError):
    """Raised when an MCP model-facing tool name is not mounted."""


@dataclass(frozen=True)
class McpToolDefinition:
    identity: McpToolIdentity
    title: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    sequential: bool = True

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("MCP tool title must not be empty")
        if not self.description:
            raise ValueError("MCP tool description must not be empty")

    @property
    def model_tool_name(self) -> str:
        return self.identity.model_tool_name


@dataclass(frozen=True)
class McpServerDefinition:
    server_id: str
    display_name: str
    tools: tuple[McpToolDefinition, ...] = ()
    resource_uri_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        make_mcp_model_tool_name(server_id=self.server_id, tool_name="server_probe")
        if not self.display_name:
            raise ValueError("MCP server display_name must not be empty")
        seen_tool_names: set[str] = set()
        for tool in self.tools:
            if tool.identity.server_id != self.server_id:
                raise ValueError(
                    f"MCP tool {tool.model_tool_name!r} does not belong to "
                    f"MCP server {self.server_id!r}"
                )
            if tool.identity.tool_name in seen_tool_names:
                raise ValueError(
                    f"Duplicate MCP tool {tool.identity.tool_name!r} on "
                    f"server {self.server_id!r}"
                )
            seen_tool_names.add(tool.identity.tool_name)


class McpManager:
    def __init__(self, servers: tuple[McpServerDefinition, ...]) -> None:
        servers_by_id: dict[str, McpServerDefinition] = {}
        for server in servers:
            if server.server_id in servers_by_id:
                raise ValueError(f"Duplicate MCP server id: {server.server_id}")
            servers_by_id[server.server_id] = server
        self._servers_by_id = servers_by_id

    def list_servers(self) -> tuple[McpServerDefinition, ...]:
        return tuple(self._servers_by_id.values())

    def get_server(self, server_id: str) -> McpServerDefinition:
        try:
            return self._servers_by_id[server_id]
        except KeyError as error:
            raise UnknownMcpServerError(f"Unknown MCP server: {server_id}") from error

    def discover_tools(
        self, *, server_id: str | None = None
    ) -> tuple[McpToolDefinition, ...]:
        if server_id is not None:
            return self.get_server(server_id).tools

        tools: list[McpToolDefinition] = []
        for server in self._servers_by_id.values():
            tools.extend(server.tools)
        return tuple(tools)

    def get_tool(self, model_tool_name: str) -> McpToolDefinition:
        identity = parse_mcp_model_tool_name(model_tool_name)
        server = self.get_server(identity.server_id)
        for tool in server.tools:
            if tool.identity.tool_name == identity.tool_name:
                return tool
        raise UnknownMcpToolError(f"Unknown MCP tool: {model_tool_name}")


DEFAULT_BUILTIN_MCP_SERVERS = (
    McpServerDefinition(
        server_id=JACA_ONBOARDING_MCP_SERVER_ID,
        display_name="JACA Onboarding",
        tools=(
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name="ask_mcq_question",
                ),
                title="Ask MCQ question",
                description="Ask one backend-rendered onboarding MCQ question.",
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name="generate_mcq_from_teaching_packets",
                ),
                title="Generate MCQ from teaching packets",
                description="Draft one MCQ from previously published teaching packets.",
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name="publish_teaching_packet",
                ),
                title="Publish teaching packet",
                description="Publish one code-grounded onboarding teaching packet.",
            ),
        ),
        resource_uri_patterns=(
            "jaca://onboarding/guide",
            "jaca://onboarding/code-mode",
            "jaca://onboarding/tools",
            "jaca://teaching-packets/{packet_id}",
        ),
    ),
)


def build_default_mcp_manager() -> McpManager:
    return McpManager(DEFAULT_BUILTIN_MCP_SERVERS)


__all__ = [
    "DEFAULT_BUILTIN_MCP_SERVERS",
    "McpManager",
    "McpManagerError",
    "McpServerDefinition",
    "McpToolDefinition",
    "UnknownMcpServerError",
    "UnknownMcpToolError",
    "build_default_mcp_manager",
]
