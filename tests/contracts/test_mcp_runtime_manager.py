from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from mcp import types as mcp_types
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from just_another_coding_agent.contracts.mcp import (
    JACA_ONBOARDING_MCP_SERVER_ID,
    McpServerConfig,
    McpStdioTransport,
    McpStreamableHttpTransport,
    McpToolCallProvenance,
    McpToolIdentity,
)
from just_another_coding_agent.contracts.run_events import (
    McpActivityDetails,
    ToolCallSucceededEvent,
)
from just_another_coding_agent.runtime import (
    build_default_mcp_manager as lazy_build_default_mcp_manager,
)
from just_another_coding_agent.runtime import (
    build_mcp_toolset as lazy_build_mcp_toolset,
)
from just_another_coding_agent.runtime.mcp import (
    DEFAULT_BUILTIN_MCP_SERVERS,
    JacaOnboardingMcpExecutor,
    McpDiscoveredTool,
    McpManager,
    McpManagerError,
    McpRuntimeFailureError,
    McpServerDefinition,
    McpToolDefinition,
    PydanticAiMcpExecutor,
    StaticMcpToolExecutor,
    UnknownMcpServerError,
    UnknownMcpToolError,
    build_configured_mcp_runtime,
    build_default_mcp_manager,
    build_effective_mcp_manager,
    build_mcp_toolset,
    build_pydantic_ai_mcp_server,
    discover_pydantic_ai_mcp_tools,
)
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps
from tests.read_only_worker_test_support import workspace_deps

_PUBLISH_TOOL_NAME = "mcp__jaca_onboarding__publish_teaching_packet"
_DEMO_ECHO_TOOL_NAME = "mcp__demo_echo__echo_message"


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

    with pytest.raises(ValueError, match="input_schema"):
        McpToolDefinition(
            identity=McpToolIdentity(server_id="demo", tool_name="echo"),
            title="Echo",
            description="Echo input.",
            input_schema={"type": "array"},
        )


def test_effective_mcp_manager_mounts_configured_discovered_tools() -> None:
    manager = build_effective_mcp_manager(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
            ),
        },
        discovered_tools_by_server={
            "demo_echo": (
                McpDiscoveredTool(
                    raw_tool_name="echo-message",
                    title="Echo message",
                    description="Echo one message.",
                    input_schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                ),
            ),
        },
    )

    assert [server.server_id for server in manager.list_servers()] == [
        "jaca_onboarding",
        "demo_echo",
    ]
    tool = manager.get_tool(_DEMO_ECHO_TOOL_NAME)
    assert tool.identity == McpToolIdentity(
        server_id="demo_echo",
        tool_name="echo_message",
    )
    assert tool.mounted_identity is not None
    assert tool.mounted_identity.raw_tool_name == "echo-message"
    assert tool.mounted_identity.model_tool_name == _DEMO_ECHO_TOOL_NAME


def test_effective_mcp_manager_filters_configured_tool_policy() -> None:
    manager = build_effective_mcp_manager(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
                enabled_tools=["echo-message"],
                disabled_tools=["skip-message"],
            ),
        },
        discovered_tools_by_server={
            "demo_echo": (
                McpDiscoveredTool(
                    raw_tool_name="echo-message",
                    title="Echo message",
                    description="Echo one message.",
                ),
                McpDiscoveredTool(
                    raw_tool_name="skip-message",
                    title="Skip message",
                    description="Skipped.",
                ),
                McpDiscoveredTool(
                    raw_tool_name="other-message",
                    title="Other message",
                    description="Not allowlisted.",
                ),
            ),
        },
    )

    assert [
        tool.model_tool_name for tool in manager.discover_tools(server_id="demo_echo")
    ] == [_DEMO_ECHO_TOOL_NAME]


def test_effective_mcp_manager_fails_hard_for_missing_discovery() -> None:
    with pytest.raises(McpManagerError, match="No discovered tools"):
        build_effective_mcp_manager(
            configured_servers={
                "demo_echo": McpServerConfig(
                    server_id="demo_echo",
                    transport=McpStreamableHttpTransport(
                        url="http://127.0.0.1:8000/mcp"
                    ),
                ),
            },
            discovered_tools_by_server={},
        )


def test_pydantic_ai_mcp_server_builder_uses_jaca_config_without_tool_prefix() -> None:
    stdio_server = build_pydantic_ai_mcp_server(
        McpServerConfig(
            server_id="demo_stdio",
            transport=McpStdioTransport(
                command="uv",
                args=["run", "demo"],
                env={"DEMO": "1"},
                cwd="/tmp/demo",
            ),
            startup_timeout_sec=2.5,
            tool_timeout_sec=7.5,
        )
    )

    assert isinstance(stdio_server, MCPServerStdio)
    assert stdio_server.id == "demo_stdio"
    assert stdio_server.tool_prefix is None
    assert stdio_server.command == "uv"
    assert stdio_server.args == ["run", "demo"]
    assert stdio_server.env == {"DEMO": "1"}
    assert stdio_server.cwd == "/tmp/demo"
    assert stdio_server.timeout == 2.5
    assert stdio_server.read_timeout == 7.5
    assert stdio_server.allow_sampling is False
    assert stdio_server.max_retries == 0


def test_pydantic_ai_mcp_server_builder_resolves_streamable_http_token() -> None:
    server = build_pydantic_ai_mcp_server(
        McpServerConfig(
            server_id="linear",
            transport=McpStreamableHttpTransport(
                url="https://mcp.linear.app/mcp",
                bearer_token_env_var="LINEAR_MCP_TOKEN",
            ),
        ),
        env={"LINEAR_MCP_TOKEN": "secret-token"},
    )

    assert isinstance(server, MCPServerStreamableHTTP)
    assert server.id == "linear"
    assert server.tool_prefix is None
    assert server.url == "https://mcp.linear.app/mcp"
    assert server.headers == {"Authorization": "Bearer secret-token"}
    assert server.allow_sampling is False
    assert server.max_retries == 0


def test_pydantic_ai_mcp_server_builder_fails_for_missing_token() -> None:
    with pytest.raises(McpManagerError, match="LINEAR_MCP_TOKEN"):
        build_pydantic_ai_mcp_server(
            McpServerConfig(
                server_id="linear",
                transport=McpStreamableHttpTransport(
                    url="https://mcp.linear.app/mcp",
                    bearer_token_env_var="LINEAR_MCP_TOKEN",
                ),
            ),
            env={},
        )


async def test_pydantic_ai_mcp_discovery_maps_raw_tools() -> None:
    server = _FakePydanticAiMcpServer(
        tools=(
            mcp_types.Tool(
                name="echo-message",
                title="Echo message",
                description="Echo one message.",
                inputSchema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            ),
        )
    )

    discovered_tools = await discover_pydantic_ai_mcp_tools(server)

    assert discovered_tools == (
        McpDiscoveredTool(
            raw_tool_name="echo-message",
            title="Echo message",
            description="Echo one message.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        ),
    )


async def test_pydantic_ai_mcp_discovery_fails_for_missing_tool_text() -> None:
    server = _FakePydanticAiMcpServer(
        tools=(
            mcp_types.Tool(
                name="echo-message",
                inputSchema={"type": "object", "properties": {}},
            ),
        )
    )

    with pytest.raises(McpManagerError, match="title"):
        await discover_pydantic_ai_mcp_tools(server)


async def test_pydantic_ai_mcp_executor_calls_raw_mounted_tool_name(tmp_path) -> None:
    pydantic_ai_server = _FakePydanticAiMcpServer(tools=())
    manager = build_effective_mcp_manager(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
            ),
        },
        discovered_tools_by_server={
            "demo_echo": (
                McpDiscoveredTool(
                    raw_tool_name="echo-message",
                    title="Echo message",
                    description="Echo one message.",
                ),
            ),
        },
        builtin_servers=(),
    )
    executor = PydanticAiMcpExecutor(
        manager=manager,
        servers_by_id={"demo_echo": pydantic_ai_server},
    )

    result = await executor.execute_tool(
        identity=McpToolIdentity(server_id="demo_echo", tool_name="echo_message"),
        arguments={"message": "hello"},
        ctx=object(),
        provenance=McpToolCallProvenance(source="top_level_model"),
    )

    assert result == {"echo": "hello"}
    assert pydantic_ai_server.calls == [
        (
            "echo-message",
            {"message": "hello"},
            {
                "jaca_model_tool_name": _DEMO_ECHO_TOOL_NAME,
                "jaca_call_source": "top_level_model",
            },
        )
    ]


async def test_configured_mcp_runtime_starts_discovers_and_closes_clients() -> None:
    fake_server = _FakePydanticAiMcpServer(
        tools=(
            mcp_types.Tool(
                name="echo-message",
                title="Echo message",
                description="Echo one message.",
                inputSchema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            ),
        )
    )

    runtime = await build_configured_mcp_runtime(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
            ),
        },
        mcp_server_factory=lambda _config: fake_server,
    )

    assert fake_server.entered == 1
    assert [tool.model_tool_name for tool in runtime.configured_tools] == [
        _DEMO_ECHO_TOOL_NAME
    ]
    assert runtime.manager.get_tool(_DEMO_ECHO_TOOL_NAME).raw_tool_name == (
        "echo-message"
    )

    await runtime.close()
    await runtime.close()

    assert fake_server.exited == 1


async def test_configured_mcp_runtime_closes_clients_after_discovery_failure() -> None:
    fake_server = _FakePydanticAiMcpServer(
        tools=(
            mcp_types.Tool(
                name="bad-message",
                inputSchema={"type": "object", "properties": {}},
            ),
        )
    )

    with pytest.raises(McpRuntimeFailureError, match="missing title") as exc_info:
        await build_configured_mcp_runtime(
            configured_servers={
                "demo_echo": McpServerConfig(
                    server_id="demo_echo",
                    transport=McpStreamableHttpTransport(
                        url="http://127.0.0.1:8000/mcp"
                    ),
                ),
            },
            mcp_server_factory=lambda _config: fake_server,
        )

    assert fake_server.entered == 1
    assert fake_server.exited == 1
    assert exc_info.value.failure.kind == "discovery_failed"
    assert exc_info.value.failure.server_id == "demo_echo"


async def test_configured_mcp_runtime_surfaces_startup_failure() -> None:
    fake_server = _FakePydanticAiMcpServer(
        tools=(),
        enter_error=RuntimeError("server unavailable"),
    )

    with pytest.raises(McpRuntimeFailureError, match="server unavailable") as exc_info:
        await build_configured_mcp_runtime(
            configured_servers={
                "demo_echo": McpServerConfig(
                    server_id="demo_echo",
                    transport=McpStreamableHttpTransport(
                        url="http://127.0.0.1:8000/mcp"
                    ),
                ),
            },
            mcp_server_factory=lambda _config: fake_server,
        )

    assert fake_server.entered == 1
    assert fake_server.exited == 0
    assert exc_info.value.failure.kind == "startup_failed"
    assert exc_info.value.failure.server_id == "demo_echo"


async def test_mcp_toolset_exposes_discovered_tools_to_model(tmp_path) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    manager = build_default_mcp_manager()
    agent = Agent(
        model,
        toolsets=[
            lazy_build_mcp_toolset(
                manager=manager,
                executor=StaticMcpToolExecutor(handlers={}),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    await agent.run("What tools are available?", deps=WorkspaceDeps(tmp_path))

    function_tools = model.last_model_request_parameters.function_tools
    assert [tool.name for tool in function_tools] == [
        "mcp__jaca_onboarding__ask_mcq_question",
        "mcp__jaca_onboarding__generate_mcq_from_teaching_packets",
        _PUBLISH_TOOL_NAME,
    ]
    publish_tool = {tool.name: tool for tool in function_tools}[_PUBLISH_TOOL_NAME]
    assert publish_tool.description == (
        "Publish one onboarding teaching packet with code-file snippets only; "
        "use docs for grounding, not snippets."
    )
    assert publish_tool.sequential is True
    assert publish_tool.parameters_json_schema["type"] == "object"
    assert publish_tool.parameters_json_schema["required"] == [
        "title",
        "concept",
        "relationships",
        "snippets",
    ]
    assert publish_tool.parameters_json_schema["additionalProperties"] is False


async def test_mcp_toolset_exposes_configured_discovered_tool_metadata(
    tmp_path,
) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    manager = build_effective_mcp_manager(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
            ),
        },
        discovered_tools_by_server={
            "demo_echo": (
                McpDiscoveredTool(
                    raw_tool_name="echo-message",
                    title="Echo message",
                    description="Echo one message.",
                ),
            ),
        },
        builtin_servers=(),
    )
    agent = Agent(
        model,
        toolsets=[
            build_mcp_toolset(
                manager=manager,
                executor=StaticMcpToolExecutor(handlers={}),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    await agent.run("What tools are available?", deps=WorkspaceDeps(tmp_path))

    function_tools = model.last_model_request_parameters.function_tools
    assert [tool.name for tool in function_tools] == [_DEMO_ECHO_TOOL_NAME]
    assert function_tools[0].metadata == {
        "mcp_server_id": "demo_echo",
        "mcp_tool_name": "echo_message",
        "raw_mcp_tool_name": "echo-message",
    }


async def test_mcp_toolset_routes_fake_tool_execution_through_stream_events(
    tmp_path,
) -> None:
    async def publish_handler(
        identity: McpToolIdentity,
        arguments: dict[str, object],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> dict[str, object]:
        assert identity.model_tool_name == _PUBLISH_TOOL_NAME
        assert arguments == {"title": "Packet"}
        assert ctx.deps.workspace_root == tmp_path
        assert provenance.source == "top_level_model"
        return {"packet_id": "packet-1", "title": arguments["title"]}

    agent = Agent(
        FunctionModel(stream_function=_call_publish_then_done),
        output_type=str,
        toolsets=[
            build_mcp_toolset(
                manager=build_default_mcp_manager(),
                executor=StaticMcpToolExecutor(
                    handlers={_PUBLISH_TOOL_NAME: publish_handler},
                ),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(tmp_path),
            available_tool_names=(_PUBLISH_TOOL_NAME,),
        )
    ]

    succeeded = next(
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    )
    assert succeeded.tool_name == _PUBLISH_TOOL_NAME
    assert succeeded.result == {"packet_id": "packet-1", "title": "Packet"}
    assert succeeded.activity is not None
    assert succeeded.activity.display_label == "MCP"
    assert isinstance(succeeded.activity.details, McpActivityDetails)
    assert succeeded.activity.details.server_id == "jaca_onboarding"
    assert succeeded.activity.details.tool_name == "publish_teaching_packet"
    assert succeeded.activity.details.failure is None


async def test_mcp_toolset_routes_configured_discovered_tool_execution(
    tmp_path,
) -> None:
    async def echo_handler(
        identity: McpToolIdentity,
        arguments: dict[str, object],
        ctx: RunContext[WorkspaceDeps],
        provenance: McpToolCallProvenance,
    ) -> dict[str, object]:
        assert identity.model_tool_name == _DEMO_ECHO_TOOL_NAME
        assert arguments == {"message": "hello"}
        assert ctx.deps.workspace_root == tmp_path
        assert provenance.source == "top_level_model"
        return {"echo": arguments["message"]}

    manager = build_effective_mcp_manager(
        configured_servers={
            "demo_echo": McpServerConfig(
                server_id="demo_echo",
                transport=McpStreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
            ),
        },
        discovered_tools_by_server={
            "demo_echo": (
                McpDiscoveredTool(
                    raw_tool_name="echo-message",
                    title="Echo message",
                    description="Echo one message.",
                    input_schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                ),
            ),
        },
        builtin_servers=(),
    )
    agent = Agent(
        FunctionModel(stream_function=_call_demo_echo_then_done),
        output_type=str,
        toolsets=[
            build_mcp_toolset(
                manager=manager,
                executor=StaticMcpToolExecutor(
                    handlers={_DEMO_ECHO_TOOL_NAME: echo_handler},
                ),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(tmp_path),
            available_tool_names=(_DEMO_ECHO_TOOL_NAME,),
        )
    ]

    succeeded = next(
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    )
    assert succeeded.tool_name == _DEMO_ECHO_TOOL_NAME
    assert succeeded.result == {"echo": "hello"}
    assert succeeded.activity is not None
    assert isinstance(succeeded.activity.details, McpActivityDetails)
    assert succeeded.activity.details.server_id == "demo_echo"
    assert succeeded.activity.details.tool_name == "echo_message"
    assert succeeded.activity.details.failure is None


async def test_onboarding_mcp_executor_routes_publish_to_native_tool(
    tmp_path,
) -> None:
    (tmp_path / "module.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    deps = replace(
        workspace_deps(tmp_path),
        session_scope=RunSessionScope(session_id="a" * 32, run_id="placeholder"),
    )
    agent = Agent(
        FunctionModel(stream_function=_call_real_publish_then_done),
        output_type=str,
        toolsets=[
            build_mcp_toolset(
                manager=build_default_mcp_manager(),
                executor=JacaOnboardingMcpExecutor(),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    try:
        events = [
            event
            async for event in stream_run_events(
                agent=agent,
                prompt="go",
                deps=deps,
                available_tool_names=(_PUBLISH_TOOL_NAME,),
            )
        ]
    finally:
        await deps.close_runtime_resources()

    succeeded = next(
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    )
    assert succeeded.tool_name == _PUBLISH_TOOL_NAME
    assert succeeded.activity is not None
    assert succeeded.activity.title == "Tool packet"
    assert succeeded.activity.display_label == "Teach"
    assert succeeded.activity.summary == "showing 2 snippets"
    assert isinstance(succeeded.activity.details, McpActivityDetails)
    assert succeeded.activity.details.model_tool_name == _PUBLISH_TOOL_NAME
    assert succeeded.activity.details.failure is None
    assert succeeded.activity.details.wrapped_title == "Tool packet"
    assert succeeded.activity.details.wrapped_display_label == "Teach"
    assert succeeded.activity.details.wrapped_summary == "showing 2 snippets"
    assert succeeded.activity.details.wrapped_details is not None
    assert succeeded.activity.details.wrapped_details.kind == "teaching_packet"
    assert isinstance(succeeded.result, dict)
    assert succeeded.result["title"] == "Tool packet"
    assert succeeded.result["concept"] == "MCP adapter"
    assert succeeded.result["snippet_count"] == 2
    assert len(succeeded.result["snippets"]) == 2
    packet_id = succeeded.result["packet_id"]
    assert isinstance(packet_id, str)
    assert deps.teaching_packet_registry.packets_by_id[packet_id].title == (
        "Tool packet"
    )


async def test_mcp_toolset_returns_typed_failure_activity_for_executor_errors(
    tmp_path,
) -> None:
    agent = Agent(
        FunctionModel(stream_function=_call_publish_then_done),
        output_type=str,
        toolsets=[
            build_mcp_toolset(
                manager=build_default_mcp_manager(),
                executor=StaticMcpToolExecutor(handlers={}),
            )
        ],
        deps_type=WorkspaceDeps,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(tmp_path),
            available_tool_names=(_PUBLISH_TOOL_NAME,),
        )
    ]

    succeeded = next(
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    )
    assert succeeded.tool_name == _PUBLISH_TOOL_NAME
    assert succeeded.result == {
        "ok": False,
        "error_type": "MissingMcpToolHandlerError",
        "message": f"No MCP execution handler for {_PUBLISH_TOOL_NAME}",
    }
    assert succeeded.activity is not None
    assert isinstance(succeeded.activity.details, McpActivityDetails)
    failure = succeeded.activity.details.failure
    assert failure is not None
    assert failure.kind == "tool_failed"
    assert failure.error_type == "MissingMcpToolHandlerError"
    assert failure.server_id == "jaca_onboarding"
    assert failure.tool_name == "publish_teaching_packet"


async def _call_publish_then_done(
    messages: object,
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name=_PUBLISH_TOOL_NAME,
                json_args='{"title": "Packet"}',
                tool_call_id="call-mcp-publish",
            )
        }
        return

    yield "done"


async def _call_real_publish_then_done(
    messages: object,
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name=_PUBLISH_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "title": "Tool packet",
                        "concept": "MCP adapter",
                        "relationships": [
                            {
                                "statement": (
                                    "The MCP executor delegates to the native "
                                    "teaching packet implementation."
                                )
                            }
                        ],
                        "snippets": [
                            {
                                "path": "module.py",
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "path": "module.py",
                                "start_line": 2,
                                "end_line": 2,
                            },
                        ],
                    }
                ),
                tool_call_id="call-mcp-publish",
            )
        }
        return

    yield "done"


async def _call_demo_echo_then_done(
    messages: object,
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name=_DEMO_ECHO_TOOL_NAME,
                json_args='{"message": "hello"}',
                tool_call_id="call-mcp-echo",
            )
        }
        return

    yield "done"


class _FakePydanticAiMcpServer:
    def __init__(
        self,
        *,
        tools: tuple[mcp_types.Tool, ...],
        enter_error: Exception | None = None,
    ) -> None:
        self._tools = tools
        self._enter_error = enter_error
        self.calls: list[tuple[str, dict[str, object], dict[str, str] | None]] = []
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> "_FakePydanticAiMcpServer":
        self.entered += 1
        if self._enter_error is not None:
            raise self._enter_error
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited += 1

    async def list_tools(self) -> list[mcp_types.Tool]:
        return list(self._tools)

    async def direct_call_tool(
        self,
        name: str,
        args: dict[str, object],
        metadata: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append((name, args, metadata))
        return {"echo": args["message"]}
