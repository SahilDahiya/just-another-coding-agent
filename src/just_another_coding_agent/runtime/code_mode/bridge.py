from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import monotonic
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field
from pydantic_ai.messages import ToolReturn

from just_another_coding_agent.contracts.run_events import CodeModeActivityDetails
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


def _metadata_title(value: Any, fallback: str) -> str:
    if isinstance(value, ToolReturn) and isinstance(value.metadata, dict):
        title = value.metadata.get("title")
        if isinstance(title, str) and title:
            return title
    return fallback


def _duration_ms_since(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))


class CodeModeToolBridge:
    def __init__(self, parent_context: CodeModeParentContext) -> None:
        self._parent_context = parent_context
        self._cell_id: str | None = None

    def bind_cell_id(self, cell_id: str) -> None:
        self._cell_id = cell_id

    def _nested_context(self, tool_name: str) -> _NestedToolContext:
        nested_deps = replace(
            self._parent_context.deps,
            tool_update_sink=None,
        )
        return _NestedToolContext(
            deps=nested_deps,
            tool_call_id=self._parent_context.tool_call_id,
            tool_name=tool_name,
        )

    async def _publish_update(
        self,
        *,
        nested_tool: str,
        nested_status: Literal["started", "succeeded", "failed"],
        title: str,
        elapsed_ms: int,
        error_type: str | None = None,
        message: str | None = None,
    ) -> None:
        sink = self._parent_context.deps.tool_update_sink
        if sink is None:
            return
        tool_call_id = self._parent_context.tool_call_id
        tool_name = self._parent_context.tool_name
        if tool_call_id is None or tool_name is None:
            return
        await sink(
            tool_call_id,
            tool_name,
            {
                "summary": f"{nested_tool} {nested_status}",
                "details": CodeModeActivityDetails(
                    cell_id=self._cell_id or "unknown",
                    nested_tool=nested_tool,
                    nested_status=nested_status,
                    title=title,
                    elapsed_ms=elapsed_ms,
                    error_type=error_type,
                    message=message,
                ).model_dump(mode="python"),
            },
        )

    async def _call_nested_tool(
        self,
        *,
        nested_tool: str,
        title: str,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        started_at = monotonic()
        await self._publish_update(
            nested_tool=nested_tool,
            nested_status="started",
            title=title,
            elapsed_ms=0,
        )
        try:
            result = await call()
        except Exception as exc:
            await self._publish_update(
                nested_tool=nested_tool,
                nested_status="failed",
                title=title,
                elapsed_ms=_duration_ms_since(started_at),
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise
        await self._publish_update(
            nested_tool=nested_tool,
            nested_status="succeeded",
            title=_metadata_title(result, title),
            elapsed_ms=_duration_ms_since(started_at),
        )
        return result

    async def read(
        self,
        *,
        path: Annotated[str, Field(min_length=1)],
        offset: Annotated[int | None, Field(ge=1)] = None,
        limit: Annotated[int | None, Field(ge=1)] = None,
    ) -> str:
        result = await self._call_nested_tool(
            nested_tool="read",
            title=f"read {path}",
            call=lambda: read(
                self._nested_context("read"),
                path=path,
                offset=offset,
                limit=limit,
            ),
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
        result = await self._call_nested_tool(
            nested_tool="grep",
            title=f"grep {pattern}",
            call=lambda: grep(
                self._nested_context("grep"),
                pattern=pattern,
                path=path,
                glob=glob,
                ignore_case=ignore_case,
                literal=literal,
                limit=limit,
            ),
        )
        return _unwrap_tool_return(result)

    async def shell(
        self,
        *,
        command: Annotated[str, Field(min_length=1)],
        timeout: Annotated[int | None, Field(gt=0)] = None,
    ) -> dict[str, int | str]:
        result = await self._call_nested_tool(
            nested_tool="shell",
            title=f"shell {command}",
            call=lambda: shell(
                self._nested_context("shell"),
                command=command,
                timeout=timeout,
            ),
        )
        return _unwrap_tool_return(result)


__all__ = ["CodeModeParentContext", "CodeModeToolBridge"]
