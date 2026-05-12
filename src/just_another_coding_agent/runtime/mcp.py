from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn
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
from just_another_coding_agent.contracts.teaching import (
    TeachingRelationship,
    TeachingSnippetRef,
)
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.mcq_from_teaching_packets import (
    generate_mcq_from_teaching_packets,
)
from just_another_coding_agent.tools.onboarding_question import ask_mcq_question
from just_another_coding_agent.tools.teaching_packet import publish_teaching_packet

_MCP_TOOL_ARGS_VALIDATOR = SchemaValidator(
    core_schema.dict_schema(
        keys_schema=core_schema.str_schema(),
        values_schema=core_schema.any_schema(),
    )
)
_ASK_MCQ_TOOL_NAME = "ask_mcq_question"
_GENERATE_MCQ_TOOL_NAME = "generate_mcq_from_teaching_packets"
_PUBLISH_TEACHING_PACKET_TOOL_NAME = "publish_teaching_packet"

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


class _OnboardingMcpModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _AskMcqQuestionArgs(_OnboardingMcpModel):
    packet_ids: list[str] = Field(min_length=1, max_length=3)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanation: str = Field(min_length=1)


class _GenerateMcqFromTeachingPacketsArgs(_OnboardingMcpModel):
    packet_ids: list[str] = Field(min_length=1, max_length=3)


class _PublishTeachingPacketArgs(_OnboardingMcpModel):
    title: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    relationships: list[TeachingRelationship] = Field(min_length=1)
    snippets: list[TeachingSnippetRef] = Field(min_length=2, max_length=5)


def _tool_return_value(value: Any) -> Any:
    if isinstance(value, ToolReturn):
        return value.return_value
    return value


def _tool_return_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, ToolReturn):
        metadata = value.metadata
        if isinstance(metadata, dict):
            return metadata
    return {}


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


class JacaOnboardingMcpExecutor:
    async def execute_tool(
        self,
        *,
        identity: McpToolIdentity,
        arguments: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> Any:
        del provenance
        if identity.server_id != JACA_ONBOARDING_MCP_SERVER_ID:
            raise UnknownMcpServerError(f"Unknown MCP server: {identity.server_id}")

        if identity.tool_name == _ASK_MCQ_TOOL_NAME:
            parsed = _AskMcqQuestionArgs.model_validate(arguments)
            return await ask_mcq_question(
                ctx,
                packet_ids=parsed.packet_ids,
                question=parsed.question,
                options=parsed.options,
                correct_index=parsed.correct_index,
                explanation=parsed.explanation,
            )

        if identity.tool_name == _GENERATE_MCQ_TOOL_NAME:
            parsed = _GenerateMcqFromTeachingPacketsArgs.model_validate(arguments)
            return await generate_mcq_from_teaching_packets(
                ctx,
                packet_ids=parsed.packet_ids,
            )

        if identity.tool_name == _PUBLISH_TEACHING_PACKET_TOOL_NAME:
            parsed = _PublishTeachingPacketArgs.model_validate(arguments)
            return await publish_teaching_packet(
                ctx,
                title=parsed.title,
                concept=parsed.concept,
                relationships=parsed.relationships,
                snippets=parsed.snippets,
            )

        raise UnknownMcpToolError(f"Unknown MCP tool: {identity.model_tool_name}")


class McpToolset(AbstractToolset[WorkspaceDeps]):
    def __init__(
        self,
        *,
        manager: McpManager,
        executor: McpToolExecutor,
        tool_names: tuple[str, ...] | None = None,
        id: str | None = "jaca_mcp",
    ) -> None:
        self._manager = manager
        self._executor = executor
        self._tool_names = tool_names
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
            for tool in self._discover_requested_tools()
        }

    def _discover_requested_tools(self) -> tuple[McpToolDefinition, ...]:
        if self._tool_names is None:
            return self._manager.discover_tools()
        return tuple(
            self._manager.get_tool(tool_name) for tool_name in self._tool_names
        )

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
            executor_result = await self._executor.execute_tool(
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

        result_metadata = _tool_return_metadata(executor_result)
        wrapped_title = result_metadata.get("title")
        wrapped_summary = result_metadata.get("summary")
        wrapped_display_label = result_metadata.get("display_label")
        wrapped_details = result_metadata.get("details")
        return make_tool_return(
            return_value=_tool_return_value(executor_result),
            title=wrapped_title if isinstance(wrapped_title, str) else definition.title,
            summary=wrapped_summary if isinstance(wrapped_summary, str) else None,
            display_label=(
                wrapped_display_label
                if isinstance(wrapped_display_label, str)
                else "MCP"
            ),
            details=McpActivityDetails(
                server_id=definition.identity.server_id,
                tool_name=definition.identity.tool_name,
                model_tool_name=definition.model_tool_name,
                provenance=provenance,
                wrapped_title=wrapped_title if isinstance(wrapped_title, str) else None,
                wrapped_summary=(
                    wrapped_summary if isinstance(wrapped_summary, str) else None
                ),
                wrapped_display_label=(
                    wrapped_display_label
                    if isinstance(wrapped_display_label, str)
                    else None
                ),
                wrapped_details=(
                    wrapped_details if isinstance(wrapped_details, dict) else None
                ),
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
                    tool_name=_ASK_MCQ_TOOL_NAME,
                ),
                title="Ask MCQ question",
                description="Ask one backend-rendered onboarding MCQ question.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "packet_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 3,
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "correct_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 3,
                        },
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "packet_ids",
                        "question",
                        "options",
                        "correct_index",
                        "explanation",
                    ],
                    "additionalProperties": False,
                },
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name=_GENERATE_MCQ_TOOL_NAME,
                ),
                title="Generate MCQ from teaching packets",
                description="Draft one MCQ from previously published teaching packets.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "packet_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 3,
                        },
                    },
                    "required": ["packet_ids"],
                    "additionalProperties": False,
                },
            ),
            McpToolDefinition(
                identity=McpToolIdentity(
                    server_id=JACA_ONBOARDING_MCP_SERVER_ID,
                    tool_name=_PUBLISH_TEACHING_PACKET_TOOL_NAME,
                ),
                title="Publish teaching packet",
                description="Publish one code-grounded onboarding teaching packet.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "concept": {"type": "string"},
                        "relationships": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "statement": {"type": "string"},
                                },
                                "required": ["statement"],
                                "additionalProperties": False,
                            },
                            "minItems": 1,
                        },
                        "snippets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "start_line": {"type": "integer", "minimum": 1},
                                    "end_line": {"type": "integer", "minimum": 1},
                                },
                                "required": ["path", "start_line", "end_line"],
                                "additionalProperties": False,
                            },
                            "minItems": 2,
                            "maxItems": 5,
                        },
                    },
                    "required": ["title", "concept", "relationships", "snippets"],
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
    tool_names: tuple[str, ...] | None = None,
) -> McpToolset:
    return McpToolset(manager=manager, executor=executor, tool_names=tool_names)


__all__ = [
    "DEFAULT_BUILTIN_MCP_SERVERS",
    "JacaOnboardingMcpExecutor",
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
