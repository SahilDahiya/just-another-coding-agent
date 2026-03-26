import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from pi_code_agent.tools.registry import (
    ToolNotImplementedError,
    UnknownToolError,
    build_canonical_toolset,
    list_canonical_tool_names,
)


def test_registry_exposes_canonical_tool_names() -> None:
    assert list_canonical_tool_names() == ("read", "write", "edit", "bash")


def test_build_canonical_toolset_rejects_unknown_tool_name() -> None:
    with pytest.raises(UnknownToolError, match="nope"):
        build_canonical_toolset(["nope"])


def test_build_canonical_toolset_rejects_unimplemented_canonical_tool() -> None:
    with pytest.raises(ToolNotImplementedError, match="write"):
        build_canonical_toolset(["write"])


def test_build_canonical_toolset_registers_read_with_pydanticai() -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(model, toolsets=[build_canonical_toolset(["read"])])

    agent.run_sync("What tools are available?")

    function_tools = model.last_model_request_parameters.function_tools
    tool_names = [tool.name for tool in function_tools]
    assert tool_names == ["read"]
