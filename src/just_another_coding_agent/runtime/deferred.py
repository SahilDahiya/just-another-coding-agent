from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ToolCallPart

from just_another_coding_agent.contracts.tools import (
    ShellToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError
from just_another_coding_agent.tools.shell import execute_shell


@dataclass(frozen=True)
class _DeferredShellExecutionContext:
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
    if call.tool_name != "shell":
        raise RuntimeError(
            f"Unsupported deferred canonical tool: {call.tool_name!r}"
        )
    if deps is None:
        raise RuntimeError("Deferred canonical shell execution requires WorkspaceDeps")

    tool_input = _validate_deferred_shell_call(call)
    ctx = _DeferredShellExecutionContext(
        deps=deps,
        tool_call_id=call.tool_call_id,
        tool_name=call.tool_name,
    )

    try:
        return await execute_shell(
            ctx=ctx,
            tool_input=tool_input,
            workspace_root=deps.workspace_root,
            shell_family=deps.shell_family,
        )
    except ToolOperationalError as error:
        return make_tool_error_result(error)


def _validate_deferred_shell_call(call: ToolCallPart) -> ShellToolInput:
    if isinstance(call.args, str):
        tool_input = ShellToolInput.model_validate_json(call.args)
    else:
        tool_input = ShellToolInput.model_validate(call.args)

    if not tool_input.defer:
        raise RuntimeError("Deferred canonical shell call must set defer=true")

    return tool_input


__all__ = ["execute_deferred_tool_requests"]
