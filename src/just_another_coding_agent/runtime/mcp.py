from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Literal, Protocol, TypeAlias
from uuid import uuid4

import httpx
from mcp import types as mcp_types
from mcp.client.auth.exceptions import OAuthFlowError, OAuthTokenError
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.mcp import (
    MCPServerStdio as PydanticAiMCPServerStdio,
)
from pydantic_ai.mcp import (
    MCPServerStreamableHTTP as PydanticAiMCPServerStreamableHTTP,
)
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from just_another_coding_agent.contracts.mcp import (
    JACA_ONBOARDING_MCP_SERVER_ID,
    McpAuthFailure,
    McpFailure,
    McpMountedToolIdentity,
    McpServerConfig,
    McpStdioTransport,
    McpStreamableHttpTransport,
    McpToolApprovalMode,
    McpToolCallProvenance,
    McpToolIdentity,
    make_mcp_model_tool_name,
    parse_mcp_model_tool_name,
)
from just_another_coding_agent.contracts.run_events import McpActivityDetails
from just_another_coding_agent.contracts.sandbox import (
    PermissionGrantApprovalRequest,
)
from just_another_coding_agent.contracts.teaching import (
    TeachingRelationship,
    TeachingSnippetRef,
)
from just_another_coding_agent.contracts.tools import make_tool_denied_result
from just_another_coding_agent.mcp_oauth import (
    McpOAuthError,
    McpOAuthLoginRequiredError,
    build_mcp_oauth_http_client,
    require_mcp_oauth_login,
)
from just_another_coding_agent.runtime.mcp_inventory import McpToolInventory
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools._approval_flow import resolve_tool_approval
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolApprovalDenied
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
_MODEL_TOOL_NAME_INVALID_CHARS = re.compile(r"[^a-z0-9_]+")
DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100

McpToolHandler = Callable[
    [McpToolIdentity, dict[str, Any], RunContext[WorkspaceDeps], McpToolCallProvenance],
    Any | Awaitable[Any],
]


@dataclass(frozen=True)
class McpToolExposure:
    direct_tool_names: tuple[str, ...]
    deferred_tool_names: tuple[str, ...]

    @property
    def search_tool_required(self) -> bool:
        return bool(self.deferred_tool_names)

    @property
    def model_visible_tool_names(self) -> tuple[str, ...]:
        if self.search_tool_required:
            return (*self.direct_tool_names, "mcp_search")
        return self.direct_tool_names


def build_mcp_tool_exposure(
    tool_names: Sequence[str],
    *,
    direct_threshold: int = DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD,
) -> McpToolExposure:
    resolved_tool_names = tuple(tool_names)
    if len(resolved_tool_names) <= direct_threshold:
        return McpToolExposure(
            direct_tool_names=resolved_tool_names,
            deferred_tool_names=(),
        )
    return McpToolExposure(
        direct_tool_names=(),
        deferred_tool_names=resolved_tool_names,
    )


class McpManagerError(RuntimeError):
    """Base error for runtime MCP manager failures."""


class UnknownMcpServerError(McpManagerError):
    """Raised when an MCP server id is not mounted in the manager."""


class UnknownMcpToolError(McpManagerError):
    """Raised when an MCP model-facing tool name is not mounted."""


class MissingMcpToolHandlerError(McpManagerError):
    """Raised when the mounted tool has no execution handler."""


class McpRuntimeFailureError(McpManagerError):
    def __init__(self, failure: McpFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


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


class PydanticAiMcpServerProtocol(Protocol):
    async def __aenter__(self) -> PydanticAiMcpServerProtocol:
        """Start the MCP client connection."""

    async def __aexit__(self, *args: Any) -> Any:
        """Close the MCP client connection."""

    async def list_tools(self) -> list[mcp_types.Tool]:
        """Return raw MCP SDK tools from a PydanticAI MCP server."""

    async def direct_call_tool(
        self,
        name: str,
        args: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Call one raw MCP tool directly on a PydanticAI MCP server."""


PydanticAiMcpServer: TypeAlias = (
    PydanticAiMCPServerStdio
    | PydanticAiMCPServerStreamableHTTP
    | PydanticAiMcpServerProtocol
)


@dataclass
class _HttpClientClosingMcpServer:
    server: PydanticAiMCPServerStreamableHTTP
    http_client: httpx.AsyncClient

    async def __aenter__(self) -> "_HttpClientClosingMcpServer":
        await self.server.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        try:
            return await self.server.__aexit__(*args)
        finally:
            await self.http_client.aclose()

    async def list_tools(self) -> list[mcp_types.Tool]:
        return await self.server.list_tools()

    async def direct_call_tool(
        self,
        name: str,
        args: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        return await self.server.direct_call_tool(name, args, metadata=metadata)


def _normalize_raw_tool_name(raw_tool_name: str) -> str:
    normalized = _MODEL_TOOL_NAME_INVALID_CHARS.sub(
        "_",
        raw_tool_name.strip().lower(),
    )
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise ValueError("MCP raw tool name cannot normalize to an empty tool name")
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    return normalized


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


def _tool_metadata(tool: McpToolDefinition) -> dict[str, str]:
    metadata = {
        "mcp_server_id": tool.identity.server_id,
        "mcp_tool_name": tool.identity.tool_name,
    }
    if tool.mounted_identity is not None:
        metadata["raw_mcp_tool_name"] = tool.mounted_identity.raw_tool_name
    return metadata


def _configured_server_allows_tool(
    config: McpServerConfig,
    raw_tool_name: str,
) -> bool:
    if config.enabled_tools is not None and raw_tool_name not in config.enabled_tools:
        return False
    if config.disabled_tools is not None and raw_tool_name in config.disabled_tools:
        return False
    return True


def _mcp_timeout(value: float | None, default: float) -> float:
    if value is None:
        return default
    return value


def _pydantic_ai_mcp_metadata(
    *,
    definition: McpToolDefinition,
    provenance: McpToolCallProvenance,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "jaca_model_tool_name": definition.model_tool_name,
        "jaca_call_source": provenance.source,
    }
    if provenance.parent_tool_call_id is not None:
        metadata["jaca_parent_tool_call_id"] = provenance.parent_tool_call_id
    if provenance.code_mode_cell_id is not None:
        metadata["jaca_code_mode_cell_id"] = provenance.code_mode_cell_id
    return metadata


def _mcp_tool_approval_mode(
    *,
    config: McpServerConfig | None,
    raw_tool_name: str,
) -> McpToolApprovalMode:
    if config is None:
        return "auto"
    tool_config = config.tools.get(raw_tool_name)
    if tool_config is not None and tool_config.approval_mode is not None:
        return tool_config.approval_mode
    if config.default_tool_approval is not None:
        return config.default_tool_approval
    return "auto"


def _mcp_approval_request(
    *,
    ctx: RunContext[WorkspaceDeps],
    definition: McpToolDefinition,
) -> PermissionGrantApprovalRequest:
    return PermissionGrantApprovalRequest(
        request_id=uuid4().hex,
        reason=f"allow MCP tool {definition.model_tool_name}",
        grant_kind="network_access",
        target=f"{definition.identity.server_id}:{definition.raw_tool_name}",
        display_subject=definition.model_tool_name,
        requested_capabilities=ctx.deps.permission_state.effective_capabilities,
    )


def _bearer_headers_from_env(
    *,
    server_id: str,
    bearer_token_env_var: str | None,
    env: Mapping[str, str],
) -> dict[str, str] | None:
    if bearer_token_env_var is None:
        return None
    token = env.get(bearer_token_env_var)
    if not token:
        recovery_hint = f"Set {bearer_token_env_var} and retry the MCP server."
        raise McpRuntimeFailureError(
            McpFailure(
                kind="auth_failed",
                error_type="McpBearerEnvMissingError",
                message=(
                    f"MCP server {server_id!r} requires environment variable "
                    f"{bearer_token_env_var!r}"
                ),
                server_id=server_id,
                auth=McpAuthFailure(
                    auth_kind="bearer_env",
                    reason="missing_bearer_env",
                    env_var=bearer_token_env_var,
                    recovery_hint=recovery_hint,
                ),
            )
        )
    return {"Authorization": f"Bearer {token}"}


def _discovered_tool_from_mcp_sdk_tool(
    mcp_tool: mcp_types.Tool,
) -> McpDiscoveredTool:
    if not mcp_tool.title:
        raise McpManagerError(f"Discovered MCP tool {mcp_tool.name!r} is missing title")
    if not mcp_tool.description:
        raise McpManagerError(
            f"Discovered MCP tool {mcp_tool.name!r} is missing description"
        )
    try:
        return McpDiscoveredTool(
            raw_tool_name=mcp_tool.name,
            title=mcp_tool.title,
            description=mcp_tool.description,
            input_schema=mcp_tool.inputSchema,
        )
    except ValueError as error:
        raise McpManagerError(
            f"Invalid discovered MCP tool {mcp_tool.name!r}: {error}"
        ) from error


def _runtime_failure(
    *,
    kind: Literal["config_failed", "startup_failed", "discovery_failed"],
    server_id: str,
    error: Exception,
) -> McpRuntimeFailureError:
    return McpRuntimeFailureError(
        McpFailure(
            kind=kind,
            error_type=type(error).__name__,
            message=str(error) or type(error).__name__,
            server_id=server_id,
        )
    )


def _oauth_runtime_failure(
    *,
    server_id: str,
    error: Exception,
    reason: Literal["oauth_login_required", "oauth_refresh_failed"] | None = None,
) -> McpRuntimeFailureError:
    resolved_reason = reason
    if resolved_reason is None:
        resolved_reason = (
            "oauth_login_required"
            if isinstance(error, McpOAuthLoginRequiredError)
            else "oauth_refresh_failed"
        )
    recovery_hint = (
        f"Run `jaca mcp login {server_id}` and retry the MCP server."
        if resolved_reason == "oauth_login_required"
        else f"Run `jaca mcp login {server_id}` again and retry the MCP server."
    )
    return McpRuntimeFailureError(
        McpFailure(
            kind="auth_failed",
            error_type=type(error).__name__,
            message=str(error) or type(error).__name__,
            server_id=server_id,
            auth=McpAuthFailure(
                auth_kind="oauth",
                reason=resolved_reason,
                recovery_hint=recovery_hint,
            ),
        )
    )


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
    mounted_identity: McpMountedToolIdentity | None = None

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("MCP tool title must not be empty")
        if not self.description:
            raise ValueError("MCP tool description must not be empty")
        if self.input_schema.get("type") != "object":
            raise ValueError("MCP tool input_schema must be an object schema")
        if (
            self.mounted_identity is not None
            and self.mounted_identity.model_identity != self.identity
        ):
            raise ValueError("MCP mounted identity must match tool identity")

    @property
    def model_tool_name(self) -> str:
        return self.identity.model_tool_name

    @property
    def raw_tool_name(self) -> str:
        if self.mounted_identity is not None:
            return self.mounted_identity.raw_tool_name
        return self.identity.tool_name


@dataclass(frozen=True)
class McpDiscoveredTool:
    raw_tool_name: str
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
        if not self.raw_tool_name:
            raise ValueError("MCP discovered raw_tool_name must not be empty")
        if not self.title:
            raise ValueError("MCP discovered tool title must not be empty")
        if not self.description:
            raise ValueError("MCP discovered tool description must not be empty")
        if self.input_schema.get("type") != "object":
            raise ValueError("MCP discovered input_schema must be an object schema")


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


@dataclass(frozen=True)
class PydanticAiMcpExecutor:
    manager: McpManager
    servers_by_id: Mapping[str, PydanticAiMcpServerProtocol]
    server_configs_by_id: Mapping[str, McpServerConfig] = field(default_factory=dict)
    _approved_model_tool_names: set[str] = field(
        default_factory=set,
        compare=False,
        repr=False,
    )

    async def execute_tool(
        self,
        *,
        identity: McpToolIdentity,
        arguments: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> Any:
        definition = self.manager.get_tool(identity.model_tool_name)
        if definition.mounted_identity is None:
            raise McpManagerError(
                "PydanticAI MCP executor can only call mounted external MCP tools"
            )
        await self._ensure_approved(
            definition=definition,
            ctx=ctx,
        )
        try:
            server = self.servers_by_id[identity.server_id]
        except KeyError as error:
            raise UnknownMcpServerError(
                f"Unknown PydanticAI MCP server: {identity.server_id}"
            ) from error
        return await server.direct_call_tool(
            definition.mounted_identity.raw_tool_name,
            arguments,
            metadata=_pydantic_ai_mcp_metadata(
                definition=definition,
                provenance=provenance,
            ),
        )

    async def _ensure_approved(
        self,
        *,
        definition: McpToolDefinition,
        ctx: RunContext[WorkspaceDeps],
    ) -> None:
        config = self.server_configs_by_id.get(definition.identity.server_id)
        mode = _mcp_tool_approval_mode(
            config=config,
            raw_tool_name=definition.raw_tool_name,
        )
        if mode == "auto":
            return
        if (
            mode == "approve"
            and definition.model_tool_name in self._approved_model_tool_names
        ):
            return
        request = _mcp_approval_request(ctx=ctx, definition=definition)
        await resolve_tool_approval(
            ctx=ctx,
            request=request,
            denied_message=(
                f"Approval denied: allow MCP tool {definition.model_tool_name}. "
                "The MCP tool was not called. Choose another approach or stop."
            ),
            missing_requester_message=(
                "MCP tool approval is required, but no approval requester is available."
            ),
        )
        if mode == "approve":
            self._approved_model_tool_names.add(definition.model_tool_name)


@dataclass(frozen=True)
class RoutingMcpToolExecutor:
    executors_by_server_id: Mapping[str, McpToolExecutor]
    fallback_executor: McpToolExecutor

    async def execute_tool(
        self,
        *,
        identity: McpToolIdentity,
        arguments: dict[str, Any],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> Any:
        executor = self.executors_by_server_id.get(
            identity.server_id,
            self.fallback_executor,
        )
        return await executor.execute_tool(
            identity=identity,
            arguments=arguments,
            ctx=ctx,
            provenance=provenance,
        )


@dataclass
class ConfiguredMcpRuntime:
    manager: McpManager
    executor: McpToolExecutor
    servers_by_id: Mapping[str, PydanticAiMcpServerProtocol]
    configured_tool_names: tuple[str, ...]
    direct_tool_names: tuple[str, ...] = ()
    deferred_tool_names: tuple[str, ...] = ()
    model_visible_tool_names: tuple[str, ...] = ()
    mcp_tool_inventory: McpToolInventory = field(default_factory=McpToolInventory)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def configured_tools(self) -> tuple[McpToolDefinition, ...]:
        return tuple(
            tool
            for tool in self.manager.discover_tools()
            if tool.identity.server_id in self.servers_by_id
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for server in reversed(tuple(self.servers_by_id.values())):
            await server.__aexit__(None, None, None)


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
        deferred_tool_names: tuple[str, ...] = (),
        id: str | None = "jaca_mcp",
    ) -> None:
        self._manager = manager
        self._executor = executor
        self._tool_names = tool_names
        self._deferred_tool_names = deferred_tool_names
        self._id = id

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(
        self,
        ctx: RunContext[WorkspaceDeps],
    ) -> dict[str, ToolsetTool[WorkspaceDeps]]:
        return {
            tool.model_tool_name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool.model_tool_name,
                    description=tool.description,
                    parameters_json_schema=tool.input_schema,
                    strict=None,
                    sequential=tool.sequential,
                    metadata=_tool_metadata(tool),
                ),
                max_retries=0,
                args_validator=_MCP_TOOL_ARGS_VALIDATOR,
            )
            for tool in self._discover_requested_tools(ctx)
        }

    def _discover_requested_tools(
        self,
        ctx: RunContext[WorkspaceDeps],
    ) -> tuple[McpToolDefinition, ...]:
        if self._tool_names is None:
            return self._manager.discover_tools()
        visible_tool_names = [*self._tool_names]
        visible_tool_names.extend(
            ctx.deps.mcp_tool_inventory.visible_deferred_tool_names()
        )
        return tuple(
            self._manager.get_tool(tool_name)
            for tool_name in visible_tool_names
            if tool_name not in self._deferred_tool_names
            or tool_name in ctx.deps.mcp_tool_inventory.activated_deferred_tool_names
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
        except ToolApprovalDenied as error:
            return make_tool_return(
                return_value=make_tool_denied_result(
                    message=str(error),
                    denial_type=error.denial_type,
                    approval_kind=error.approval_kind,
                    subject=error.subject,
                    retry_same_request_allowed=error.retry_same_request_allowed,
                ),
                title=definition.title,
                summary=str(error),
                display_label="MCP",
                details=McpActivityDetails(
                    server_id=definition.identity.server_id,
                    tool_name=definition.identity.tool_name,
                    model_tool_name=definition.model_tool_name,
                    provenance=provenance,
                ),
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
                description=(
                    "Publish one onboarding teaching packet with code-file "
                    "snippets only; use docs for grounding, not snippets."
                ),
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


def _server_definition_from_config(
    config: McpServerConfig,
    discovered_tools: Sequence[McpDiscoveredTool],
) -> McpServerDefinition:
    tools: list[McpToolDefinition] = []
    seen_model_tool_names: set[str] = set()
    for discovered_tool in discovered_tools:
        if not _configured_server_allows_tool(config, discovered_tool.raw_tool_name):
            continue

        normalized_tool_name = _normalize_raw_tool_name(discovered_tool.raw_tool_name)
        model_tool_name = make_mcp_model_tool_name(
            server_id=config.server_id,
            tool_name=normalized_tool_name,
        )
        if model_tool_name in seen_model_tool_names:
            raise McpManagerError(
                "Discovered MCP tools normalize to the same model tool name: "
                f"{model_tool_name}"
            )
        seen_model_tool_names.add(model_tool_name)
        mounted_identity = McpMountedToolIdentity(
            server_id=config.server_id,
            raw_tool_name=discovered_tool.raw_tool_name,
            model_tool_name=model_tool_name,
        )
        tools.append(
            McpToolDefinition(
                identity=mounted_identity.model_identity,
                title=discovered_tool.title,
                description=discovered_tool.description,
                input_schema=discovered_tool.input_schema,
                sequential=discovered_tool.sequential,
                mounted_identity=mounted_identity,
            )
        )

    return McpServerDefinition(
        server_id=config.server_id,
        display_name=config.server_id,
        tools=tuple(tools),
    )


def build_effective_mcp_manager(
    *,
    configured_servers: Mapping[str, McpServerConfig],
    discovered_tools_by_server: Mapping[str, Sequence[McpDiscoveredTool]],
    builtin_servers: tuple[McpServerDefinition, ...] = DEFAULT_BUILTIN_MCP_SERVERS,
) -> McpManager:
    servers = list(builtin_servers)
    for server_id, config in configured_servers.items():
        if server_id != config.server_id:
            raise ValueError(
                "Configured MCP server mapping key must match config.server_id: "
                f"{server_id!r} != {config.server_id!r}"
            )
        if not config.enabled:
            continue
        try:
            discovered_tools = discovered_tools_by_server[server_id]
        except KeyError as error:
            raise McpManagerError(
                f"No discovered tools for MCP server: {server_id}"
            ) from error
        servers.append(_server_definition_from_config(config, discovered_tools))
    return McpManager(tuple(servers))


def build_pydantic_ai_mcp_server(
    config: McpServerConfig,
    *,
    env: Mapping[str, str] | None = None,
) -> PydanticAiMCPServerStdio | PydanticAiMCPServerStreamableHTTP:
    resolved_env = os.environ if env is None else env
    timeout = _mcp_timeout(config.startup_timeout_sec, default=5)
    read_timeout = _mcp_timeout(config.tool_timeout_sec, default=5 * 60)
    transport = config.transport
    if isinstance(transport, McpStdioTransport):
        return PydanticAiMCPServerStdio(
            transport.command,
            args=transport.args,
            env=transport.env,
            cwd=transport.cwd,
            id=config.server_id,
            tool_prefix=None,
            timeout=timeout,
            read_timeout=read_timeout,
            allow_sampling=False,
            max_retries=0,
        )
    if isinstance(transport, McpStreamableHttpTransport):
        if transport.oauth is not None:
            try:
                require_mcp_oauth_login(config)
            except McpOAuthError as error:
                raise _oauth_runtime_failure(
                    server_id=config.server_id,
                    error=error,
                ) from error
            http_client = build_mcp_oauth_http_client(config)
            return _HttpClientClosingMcpServer(
                server=PydanticAiMCPServerStreamableHTTP(
                    transport.url,
                    http_client=http_client,
                    id=config.server_id,
                    tool_prefix=None,
                    timeout=timeout,
                    read_timeout=read_timeout,
                    allow_sampling=False,
                    max_retries=0,
                ),
                http_client=http_client,
            )
        return PydanticAiMCPServerStreamableHTTP(
            transport.url,
            headers=_bearer_headers_from_env(
                server_id=config.server_id,
                bearer_token_env_var=transport.bearer_token_env_var,
                env=resolved_env,
            ),
            id=config.server_id,
            tool_prefix=None,
            timeout=timeout,
            read_timeout=read_timeout,
            allow_sampling=False,
            max_retries=0,
        )
    raise TypeError(f"Unsupported MCP transport: {type(transport).__name__}")


async def discover_pydantic_ai_mcp_tools(
    server: PydanticAiMcpServerProtocol,
) -> tuple[McpDiscoveredTool, ...]:
    return tuple(
        _discovered_tool_from_mcp_sdk_tool(mcp_tool)
        for mcp_tool in await server.list_tools()
    )


async def build_configured_mcp_runtime(
    *,
    configured_servers: Mapping[str, McpServerConfig],
    mcp_server_factory: (
        Callable[[McpServerConfig], PydanticAiMcpServerProtocol] | None
    ) = None,
) -> ConfiguredMcpRuntime:
    factory = (
        build_pydantic_ai_mcp_server
        if mcp_server_factory is None
        else mcp_server_factory
    )
    servers_by_id: dict[str, PydanticAiMcpServerProtocol] = {}
    discovered_tools_by_server: dict[str, tuple[McpDiscoveredTool, ...]] = {}
    try:
        for server_id, config in configured_servers.items():
            if server_id != config.server_id:
                raise ValueError(
                    "Configured MCP server mapping key must match config.server_id: "
                    f"{server_id!r} != {config.server_id!r}"
                )
            if not config.enabled:
                continue
            try:
                server = factory(config)
            except McpRuntimeFailureError:
                raise
            except Exception as error:
                raise _runtime_failure(
                    kind="config_failed",
                    server_id=server_id,
                    error=error,
                ) from error
            try:
                await server.__aenter__()
            except (OAuthFlowError, OAuthTokenError) as error:
                raise _oauth_runtime_failure(
                    server_id=server_id,
                    error=error,
                    reason="oauth_refresh_failed",
                ) from error
            except Exception as error:
                raise _runtime_failure(
                    kind="startup_failed",
                    server_id=server_id,
                    error=error,
                ) from error
            servers_by_id[server_id] = server
            try:
                discovered_tools_by_server[
                    server_id
                ] = await discover_pydantic_ai_mcp_tools(server)
            except Exception as error:
                raise _runtime_failure(
                    kind="discovery_failed",
                    server_id=server_id,
                    error=error,
                ) from error
        try:
            manager = build_effective_mcp_manager(
                configured_servers=configured_servers,
                discovered_tools_by_server=discovered_tools_by_server,
            )
        except Exception as error:
            raise _runtime_failure(
                kind="discovery_failed",
                server_id=next(iter(discovered_tools_by_server), "unknown"),
                error=error,
            ) from error
        external_executor = PydanticAiMcpExecutor(
            manager=manager,
            servers_by_id=servers_by_id,
            server_configs_by_id=configured_servers,
        )
        executor = RoutingMcpToolExecutor(
            executors_by_server_id={
                server_id: external_executor for server_id in servers_by_id
            },
            fallback_executor=JacaOnboardingMcpExecutor(),
        )
        configured_tool_names = tuple(
            tool.model_tool_name
            for tool in manager.discover_tools()
            if tool.identity.server_id in servers_by_id
        )
        exposure = build_mcp_tool_exposure(configured_tool_names)
        mcp_tool_inventory = McpToolInventory.from_manager(
            manager,
            direct_tool_names=exposure.direct_tool_names,
            deferred_tool_names=exposure.deferred_tool_names,
        )
        return ConfiguredMcpRuntime(
            manager=manager,
            executor=executor,
            servers_by_id=servers_by_id,
            configured_tool_names=configured_tool_names,
            direct_tool_names=exposure.direct_tool_names,
            deferred_tool_names=exposure.deferred_tool_names,
            model_visible_tool_names=exposure.model_visible_tool_names,
            mcp_tool_inventory=mcp_tool_inventory,
        )
    except Exception:
        for server in reversed(tuple(servers_by_id.values())):
            await server.__aexit__(None, None, None)
        raise


def build_default_mcp_manager() -> McpManager:
    return McpManager(DEFAULT_BUILTIN_MCP_SERVERS)


def build_mcp_toolset(
    *,
    manager: McpManager,
    executor: McpToolExecutor,
    tool_names: tuple[str, ...] | None = None,
    deferred_tool_names: tuple[str, ...] = (),
) -> McpToolset:
    return McpToolset(
        manager=manager,
        executor=executor,
        tool_names=tool_names,
        deferred_tool_names=deferred_tool_names,
    )


__all__ = [
    "ConfiguredMcpRuntime",
    "DEFAULT_BUILTIN_MCP_SERVERS",
    "JacaOnboardingMcpExecutor",
    "McpDiscoveredTool",
    "McpManagerError",
    "McpManager",
    "McpToolExposure",
    "McpRuntimeFailureError",
    "PydanticAiMcpExecutor",
    "PydanticAiMcpServer",
    "PydanticAiMcpServerProtocol",
    "RoutingMcpToolExecutor",
    "McpServerDefinition",
    "McpToolExecutor",
    "McpToolDefinition",
    "McpToolHandler",
    "McpToolset",
    "MissingMcpToolHandlerError",
    "StaticMcpToolExecutor",
    "UnknownMcpServerError",
    "UnknownMcpToolError",
    "build_configured_mcp_runtime",
    "build_default_mcp_manager",
    "build_effective_mcp_manager",
    "build_mcp_tool_exposure",
    "build_mcp_toolset",
    "build_pydantic_ai_mcp_server",
    "discover_pydantic_ai_mcp_tools",
]
