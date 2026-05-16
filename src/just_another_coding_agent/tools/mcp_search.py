from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import WorkspaceDeps

MCP_SEARCH_TOOL_NAME = "mcp_search"
MCP_SEARCH_DEFAULT_LIMIT = 10
MCP_SEARCH_MAX_LIMIT = 20


async def mcp_search(
    ctx: RunContext[WorkspaceDeps],
    query: Annotated[str, Field(min_length=1)],
    limit: Annotated[int, Field(ge=1, le=MCP_SEARCH_MAX_LIMIT)] = (
        MCP_SEARCH_DEFAULT_LIMIT
    ),
):
    """Search deferred MCP tools and enable returned tools for this run.

    Args:
        query: Text to match against MCP tool names, titles, and descriptions.
        limit: Maximum number of matching tools to return and enable.
    """
    inventory = ctx.deps.mcp_tool_inventory
    matches = inventory.search(query=query, limit=limit)
    for item in matches:
        inventory.activate_tool(item.name)
    result = {
        "matches": [item.as_result() for item in matches],
        "result_count": len(matches),
        "activated_tool_names": [item.name for item in matches if item.deferred],
    }
    return make_tool_return(
        return_value=result,
        title=f"mcp_search {query}",
        summary=f"{len(matches)} MCP tool match{'es' if len(matches) != 1 else ''}",
        display_label="MCP",
        details=None,
    )


MCP_SEARCH_TOOL = Tool(
    mcp_search,
    takes_ctx=True,
    name=MCP_SEARCH_TOOL_NAME,
    description=(
        "Search deferred MCP tools by name, title, or description. Returns exact "
        "mcp__server__tool names and enables returned deferred tools for this run."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = [
    "MCP_SEARCH_DEFAULT_LIMIT",
    "MCP_SEARCH_MAX_LIMIT",
    "MCP_SEARCH_TOOL",
    "MCP_SEARCH_TOOL_NAME",
    "mcp_search",
]
