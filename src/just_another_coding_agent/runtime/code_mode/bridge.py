from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from pydantic import Field
from pydantic_ai.messages import ToolReturn

from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.grep import GREP_MAX_MATCHES, grep
from just_another_coding_agent.tools.read import read
from just_another_coding_agent.tools.shell import shell


class CodeModeParentContext(Protocol):
    deps: WorkspaceDeps
    tool_call_id: str | None
    tool_name: str | None


@dataclass(frozen=True)
class _NestedToolContext:
    deps: WorkspaceDeps
    tool_call_id: str | None
    tool_name: str | None


def _unwrap_tool_return(value: Any) -> Any:
    if isinstance(value, ToolReturn):
        return value.return_value
    return value


class CodeModeToolBridge:
    def __init__(self, parent_context: CodeModeParentContext) -> None:
        self._parent_context = parent_context

    def _nested_context(self, tool_name: str) -> _NestedToolContext:
        return _NestedToolContext(
            deps=self._parent_context.deps,
            tool_call_id=self._parent_context.tool_call_id,
            tool_name=tool_name,
        )

    async def read(
        self,
        *,
        path: Annotated[str, Field(min_length=1)],
        offset: Annotated[int | None, Field(ge=1)] = None,
        limit: Annotated[int | None, Field(ge=1)] = None,
    ) -> str:
        result = await read(
            self._nested_context("read"),
            path=path,
            offset=offset,
            limit=limit,
        )
        return _unwrap_tool_return(result)

    async def grep(
        self,
        *,
        pattern: Annotated[str, Field(min_length=1)],
        path: Annotated[str | None, Field(min_length=1)] = None,
        glob: Annotated[str | None, Field(min_length=1)] = None,
        ignore_case: bool = False,
        literal: bool = False,
        limit: Annotated[int, Field(ge=1)] = GREP_MAX_MATCHES,
    ) -> str:
        result = await grep(
            self._nested_context("grep"),
            pattern=pattern,
            path=path,
            glob=glob,
            ignore_case=ignore_case,
            literal=literal,
            limit=limit,
        )
        return _unwrap_tool_return(result)

    async def shell(
        self,
        *,
        command: Annotated[str, Field(min_length=1)],
        timeout: Annotated[int | None, Field(gt=0)] = None,
    ) -> dict[str, int | str]:
        result = await shell(
            self._nested_context("shell"),
            command=command,
            timeout=timeout,
        )
        return _unwrap_tool_return(result)


__all__ = ["CodeModeParentContext", "CodeModeToolBridge"]
