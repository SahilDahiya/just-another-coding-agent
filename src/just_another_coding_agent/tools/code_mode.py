from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.code_mode import (
    CodeModeExecRequest,
    CodeModeWaitRequest,
)
from just_another_coding_agent.runtime.code_mode.bridge import CodeModeToolBridge
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import WorkspaceDeps


def _result_payload(result) -> dict[str, object]:
    return result.model_dump(mode="json")


def _summary_for_state(state: str) -> str:
    return f"code cell {state}"


async def code_mode_exec(
    ctx: RunContext[WorkspaceDeps],
    source: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Code Mode source text to execute in a run-local cell. The "
                "cell may call canonical tools only through the Code Mode "
                "bridge."
            ),
        ),
    ],
    yield_time_ms: Annotated[
        int | None,
        Field(
            ge=0,
            description=(
                "Optional milliseconds to wait for output before returning a "
                "yielded cell result."
            ),
        ),
    ] = None,
    max_output_tokens: Annotated[
        int | None,
        Field(
            ge=1,
            description="Optional maximum output budget for the cell result.",
        ),
    ] = None,
    timeout_ms: Annotated[
        int | None,
        Field(
            gt=0,
            description="Optional total timeout in milliseconds for the cell.",
        ),
    ] = None,
):
    """Start one run-local Code Mode cell.

    Args:
        source: Code Mode source text to execute.
        yield_time_ms: Optional milliseconds to wait before yielding.
        max_output_tokens: Optional maximum output budget.
        timeout_ms: Optional total timeout in milliseconds.
    """

    if ctx.deps.code_mode_runner is None:
        raise RuntimeError("Code Mode runner is not configured")

    result = await ctx.deps.code_mode_cell_service.start_cell(
        CodeModeExecRequest(
            source=source,
            yield_time_ms=yield_time_ms,
            max_output_tokens=max_output_tokens,
            timeout_ms=timeout_ms,
        ),
        ctx.deps.code_mode_runner,
        tools=CodeModeToolBridge(ctx),
    )
    payload = _result_payload(result)
    return make_tool_return(
        return_value=payload,
        title="exec code cell",
        summary=_summary_for_state(result.state),
        display_label="Code",
        details=None,
    )


async def code_mode_wait(
    ctx: RunContext[WorkspaceDeps],
    cell_id: Annotated[
        str,
        Field(
            min_length=1,
            description="Identifier of the yielded Code Mode cell.",
        ),
    ],
    yield_time_ms: Annotated[
        int | None,
        Field(
            ge=0,
            description=(
                "Optional milliseconds to wait for more output before "
                "yielding again."
            ),
        ),
    ] = None,
    max_output_tokens: Annotated[
        int | None,
        Field(
            ge=1,
            description="Optional maximum output budget for this wait result.",
        ),
    ] = None,
    terminate: Annotated[
        bool,
        Field(description="Whether to terminate the running cell."),
    ] = False,
):
    """Poll, wait for, or terminate a yielded Code Mode cell.

    Args:
        cell_id: Identifier of the yielded Code Mode cell.
        yield_time_ms: Optional milliseconds to wait before yielding again.
        max_output_tokens: Optional maximum output budget for this wait result.
        terminate: Whether to terminate the running cell.
    """

    result = await ctx.deps.code_mode_cell_service.wait_cell(
        CodeModeWaitRequest(
            cell_id=cell_id,
            yield_time_ms=yield_time_ms,
            max_output_tokens=max_output_tokens,
            terminate=terminate,
        )
    )
    payload = _result_payload(result)
    return make_tool_return(
        return_value=payload,
        title="wait code cell",
        summary=_summary_for_state(result.state),
        display_label="Code",
        details=None,
    )


CODE_MODE_EXEC_TOOL = Tool(
    code_mode_exec,
    takes_ctx=True,
    name="exec",
    description=(
        "Start one run-local Code Mode cell. The cell executes through the "
        "backend-owned Code Mode service and may call canonical tools only "
        "through the Code Mode bridge."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

CODE_MODE_WAIT_TOOL = Tool(
    code_mode_wait,
    takes_ctx=True,
    name="wait",
    description="Poll, wait for, or terminate a yielded Code Mode cell by cell_id.",
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

__all__ = [
    "CODE_MODE_EXEC_TOOL",
    "CODE_MODE_WAIT_TOOL",
    "code_mode_exec",
    "code_mode_wait",
]
