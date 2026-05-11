from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from just_another_coding_agent.contracts.mcp import (
    JACA_ONBOARDING_MCP_SERVER_ID,
    McpFailure,
    McpToolCallProvenance,
    McpToolIdentity,
    make_mcp_model_tool_name,
    parse_mcp_model_tool_name,
)
from just_another_coding_agent.contracts.run_events import McpActivityDetails
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import WorkspaceDeps

_MCP_TOOL_ARGS_VALIDATOR = SchemaValidator(
    core_schema.dict_schema(
        keys_schema=core_schema.str_schema(),
        values_schema=core_schema.any_schema(),
    )
)

McpToolHandler = Callable[
    [McpToolIdentity, dict[str, Any], RunContext[WorkspaceDeps], McpToolCallProvenance],
    Any | Awaitable[Any],
]


class McpManagerError(RuntimeError):
    """Base error for runtime MCP manager failures."""


class UnknownMcpServerError(McpManagerError):
    """Raised when an MCP server id is not mounted in the manager."""


class UnknownMcpToolError(McpManagerError):
    """Raised when an MCP model-facing tool name is not mounted."""


class MissingMcpToolHandlerError(McpManagerError):
    """Raised when the mounted tool has no execution handler."""


class McpToolExecutor(Protocol):
    async def execute_tool(
        self,
        *,
        identity: McpToolIdentity,
        arguments: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> Any:
        """Execute one resolved MCP tool."""


@dataclass(frozen=True)
class McpToolDefinition:
    identity: McpToolIdentity
    title: str
    description: str
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    sequential: bool = True

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("MCP tool title must not be empty")
        if not self.description:
            raise ValueError("MCP tool description must not be empty")
        if self.input_schema.get("type") != "object":
            raise ValueError("MCP tool input_schema must be an object schema")

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


@dataclass(frozen=True)
class StaticMcpToolExecutor:
    handlers: dict[str, McpToolHandler]

    async def execute_tool(
        self,
        *,
        identity: McpToolIdentity,
        arguments: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> Any:
        model_tool_name = identity.model_tool_name
        try:
            handler = self.handlers[model_tool_name]
        except KeyError as error:
            raise MissingMcpToolHandlerError(
                f"No MCP execution handler for {model_tool_name}"
            ) from error

        result = handler(identity, arguments, ctx, provenance)
        if isawaitable(result):
            return await result
        return result


class McpToolset(AbstractToolset[WorkspaceDeps]):
    def __init__(
        self,
        *,
        manager: McpManager,
        executor: McpToolExecutor,
        id: str | None = "jaca_mcp",
    ) -> None:
        self._manager = manager
        self._executor = executor
        self._id = id

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(
        self,
        ctx: RunContext[WorkspaceDeps],
    ) -> dict[str, ToolsetTool[WorkspaceDeps]]:
        del ctx
        return {
            tool.model_tool_name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool.model_tool_name,
                    description=tool.description,
                    parameters_json_schema=tool.input_schema,
                    strict=None,
                    sequential=tool.sequential,
                    metadata={
                        "mcp_server_id": tool.identity.server_id,
                        "mcp_tool_name": tool.identity.tool_name,
                    },
                ),
                max_retries=0,
                args_validator=_MCP_TOOL_ARGS_VALIDATOR,
            )
            for tool in self._manager.discover_tools()
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        tool: ToolsetTool[WorkspaceDeps],
    ) -> Any:
        del tool
        definition = self._manager.get_tool(name)
        provenance = McpToolCallProvenance(source="top_level_model")
        try:
            result = await self._executor.execute_tool(
                identity=definition.identity,
                arguments=tool_args,
                ctx=ctx,
                provenance=provenance,
            )
        except Exception as error:
            failure = McpFailure(
                kind="tool_failed",
                error_type=type(error).__name__,
                message=str(error) or type(error).__name__,
                server_id=definition.identity.server_id,
                tool_name=definition.identity.tool_name,
            )
            return make_tool_return(
                return_value={
                    "ok": False,
                    "error_type": failure.error_type,
                    "message": failure.message,
                },
                title=definition.title,
                summary=failure.message,
                display_label="MCP",
                details=McpActivityDetails(
                    server_id=definition.identity.server_id,
                    tool_name=definition.identity.tool_name,
                    model_tool_name=definition.model_tool_name,
                    provenance=provenance,
                    failure=failure,
                ),
            )

        return make_tool_return(
            return_value=result,
            title=definition.title,
            summary=None,
            display_label="MCP",
            details=McpActivityDetails(
                server_id=definition.identity.server_id,
                tool_name=definition.identity.tool_name,
                model_tool_name=definition.model_tool_name,
                provenance=provenance,
            ),
        )


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
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["question", "options"],
                    "additionalProperties": False,
                },
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name="generate_mcq_from_teaching_packets",
                ),
                title="Generate MCQ from teaching packets",
                description="Draft one MCQ from previously published teaching packets.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "packet_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["packet_ids"],
                    "additionalProperties": False,
                },
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name="publish_teaching_packet",
                ),
                title="Publish teaching packet",
                description="Publish one code-grounded onboarding teaching packet.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
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


def build_mcp_toolset(
    *,
    manager: McpManager,
    executor: McpToolExecutor,
) -> McpToolset:
    return McpToolset(manager=manager, executor=executor)


__all__ = [
    "DEFAULT_BUILTIN_MCP_SERVERS",
    "McpManagerError",
    "McpManager",
    "McpServerDefinition",
    "McpToolExecutor",
    "McpToolDefinition",
    "McpToolHandler",
    "McpToolset",
    "MissingMcpToolHandlerError",
    "StaticMcpToolExecutor",
    "UnknownMcpServerError",
    "UnknownMcpToolError",
    "build_default_mcp_manager",
    "build_mcp_toolset",
]
