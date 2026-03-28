from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ToolCallPart

from just_another_coding_agent.contracts.tools import (
    BashToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools.bash import execute_bash
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError


@dataclass(frozen=True)
class _DeferredBashExecutionContext:
    deps: WorkspaceDeps
    tool_call_id: str
    tool_name: str


async def execute_deferred_tool_requests(
    *,
    requests: DeferredToolRequests,
    deps: WorkspaceDeps | None,
) -> DeferredToolResults:
    if requests.approvals:
        raise RuntimeError(
            "Canonical runtime does not support approval-required deferred tools"
        )

    results = DeferredToolResults()
    for call in requests.calls:
        results.calls[call.tool_call_id] = await _execute_deferred_tool_call(
            call=call,
            deps=deps,
        )
    return results


async def _execute_deferred_tool_call(
    *,
    call: ToolCallPart,
    deps: WorkspaceDeps | None,
) -> Any:
    if call.tool_name != "bash":
        raise RuntimeError(
            f"Unsupported deferred canonical tool: {call.tool_name!r}"
        )
    if deps is None:
        raise RuntimeError("Deferred canonical bash execution requires WorkspaceDeps")

    tool_input = _validate_deferred_bash_call(call)
    ctx = _DeferredBashExecutionContext(
        deps=deps,
        tool_call_id=call.tool_call_id,
        tool_name=call.tool_name,
    )

    try:
        return await execute_bash(
            ctx=ctx,
            tool_input=tool_input,
            workspace_root=deps.workspace_root,
        )
    except ToolOperationalError as error:
        return make_tool_error_result(error)


def _validate_deferred_bash_call(call: ToolCallPart) -> BashToolInput:
    if isinstance(call.args, str):
        tool_input = BashToolInput.model_validate_json(call.args)
    else:
        tool_input = BashToolInput.model_validate(call.args)

    if not tool_input.defer:
        raise RuntimeError(
            "Deferred canonical bash call must set defer=true"
        )

    return tool_input


__all__ = ["execute_deferred_tool_requests"]
