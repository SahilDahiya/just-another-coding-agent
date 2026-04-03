from __future__ import annotations

from collections.abc import AsyncIterable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai.capabilities.abstract import AbstractCapability
from pydantic_ai.messages import AgentStreamEvent, FunctionToolCallEvent, ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition

from just_another_coding_agent.tools.deps import WorkspaceDeps


@dataclass
class CanonicalValidatedToolArgsCapability(AbstractCapability[WorkspaceDeps]):
    """Make validated tool args the canonical streamed tool-call payload."""

    _validated_args_by_tool_call_id: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    async def for_run(
        self,
        ctx: RunContext[WorkspaceDeps],
    ) -> CanonicalValidatedToolArgsCapability:
        del ctx
        return type(self)()

    async def after_tool_validate(
        self,
        ctx: RunContext[WorkspaceDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        del ctx, tool_def
        self._validated_args_by_tool_call_id[call.tool_call_id] = deepcopy(args)
        return args

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[WorkspaceDeps],
        *,
        stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        del ctx
        async for event in stream:
            if not isinstance(event, FunctionToolCallEvent):
                yield event
                continue

            if event.args_valid is False:
                yield replace(event, part=replace(event.part, args=None))
                continue

            if event.args_valid is True:
                validated_args = self._validated_args_by_tool_call_id.get(
                    event.part.tool_call_id
                )
                if validated_args is None:
                    raise RuntimeError(
                        "Validated tool call event missing canonical validated args: "
                        f"{event.part.tool_call_id}"
                    )
                yield replace(
                    event,
                    part=replace(event.part, args=deepcopy(validated_args)),
                )
                continue

            yield event


__all__ = ["CanonicalValidatedToolArgsCapability"]
