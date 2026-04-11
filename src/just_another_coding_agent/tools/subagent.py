from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import (
    RunFailedEvent,
    RunSucceededEvent,
    SubagentActivityDetails,
    ToolCallStartedEvent,
)
from just_another_coding_agent.contracts.session import SessionName
from just_another_coding_agent.runtime.subagent import (
    EphemeralSubagentSpec,
    SubagentCapability,
    SubagentRole,
    stream_ephemeral_subagent_run_events,
)
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError

_DISPLAY_LABEL_BY_ROLE: dict[SubagentRole, str] = {
    "general": "Subagent",
    "explore": "Explore",
    "verification": "Verify",
}
_RUNNING_SUMMARY_BY_ROLE: dict[SubagentRole, str] = {
    "general": "running child task",
    "explore": "exploring repository",
    "verification": "verifying repository state",
}
_MAX_RUNNING_PREVIEW_LINES = 2


class SubagentToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ok: Literal[True] = True
    name: SessionName
    role: SubagentRole
    capability: SubagentCapability
    summary_text: str
    output_text: str


def _subagent_summary(summary_text: str) -> str:
    normalized = " ".join(summary_text.split())
    if not normalized:
        return "subagent completed"
    return truncate_activity_label(normalized, limit=88)


def _normalize_subagent_output_text(output_text: str) -> str:
    if not output_text.strip():
        raise ToolOperationalError("Subagent returned empty output")
    return output_text


def _build_subagent_summary_text(output_text: str) -> str:
    for line in output_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        summary = stripped.lstrip("#*- ").strip()
        if summary:
            return summary
    return "subagent completed"


def _display_label_for_role(role: SubagentRole) -> str:
    return _DISPLAY_LABEL_BY_ROLE[role]


def _running_summary_for_role(role: SubagentRole) -> str:
    return _RUNNING_SUMMARY_BY_ROLE[role]


def _subagent_details(
    *,
    name: SessionName,
    role: SubagentRole,
    preview_lines: list[str],
    preview_terminal: bool,
) -> SubagentActivityDetails:
    return SubagentActivityDetails(
        name=name,
        role=role,
        preview_lines=preview_lines,
        preview_terminal=preview_terminal,
    )


def _append_running_preview_line(
    preview_lines: list[str],
    line: str,
) -> list[str]:
    normalized = truncate_activity_label(line, limit=88)
    if not normalized:
        return preview_lines
    if preview_lines and preview_lines[-1] == normalized:
        return preview_lines
    next_lines = [*preview_lines, normalized]
    if len(next_lines) > _MAX_RUNNING_PREVIEW_LINES:
        return next_lines[-_MAX_RUNNING_PREVIEW_LINES:]
    return next_lines


def _build_terminal_preview_lines(
    preview_lines: list[str],
    final_line: str,
) -> list[str]:
    normalized = truncate_activity_label(final_line, limit=88)
    if not normalized:
        return preview_lines[-_MAX_RUNNING_PREVIEW_LINES:]
    return [
        *preview_lines[-_MAX_RUNNING_PREVIEW_LINES:],
        normalized,
    ]


def _preview_line_from_child_started_event(
    event: ToolCallStartedEvent,
) -> str | None:
    if event.activity is not None and event.activity.title:
        return event.activity.title
    return event.tool_name


async def _publish_subagent_update(
    *,
    ctx: RunContext[WorkspaceDeps],
    name: SessionName,
    role: SubagentRole,
    summary: str,
    preview_lines: list[str],
    preview_terminal: bool = False,
) -> None:
    if ctx.deps.tool_update_sink is None:
        return
    if ctx.tool_call_id is None or ctx.tool_name is None:
        return
    await ctx.deps.tool_update_sink(
        ctx.tool_call_id,
        ctx.tool_name,
        {
            "summary": summary,
            "details": _subagent_details(
                name=name,
                role=role,
                preview_lines=preview_lines,
                preview_terminal=preview_terminal,
            ).model_dump(mode="python"),
        },
    )


async def subagent(
    ctx: RunContext[WorkspaceDeps],
    name: Annotated[
        SessionName,
        Field(description="Short kebab-case session name for the child run."),
    ],
    task: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Bounded task for the child run to complete. Include the "
                "exact goal, relevant files or artifacts, constraints, stop "
                "condition, and desired report shape when needed."
            ),
        ),
    ],
    role: Annotated[
        SubagentRole,
        Field(
            description=(
                "Child role. Use explore for investigation or verification "
                "for explicit cross-checking."
            )
        ),
    ] = "general",
    capability: Annotated[
        SubagentCapability,
        Field(
            description=(
                "Child tool capability. Use 'default' for read/grep/find/ls "
                "only or 'shell' when the child also needs shell commands."
            )
        ),
    ] = "default",
) -> dict[str, object]:
    """Run one ephemeral subagent for a bounded side task.

    Args:
        name: Short kebab-case session name for the child run.
        task: Bounded task for the child run to complete.
        role: Child role. Use explore for investigation or verification
            for explicit cross-checking.
        capability: Child tool capability for the child run.
    """

    if ctx.deps.session_scope.kind != "root":
        raise ToolOperationalError(
            "Subagent spawning is only available to root runs."
        )
    if ctx.deps.run_frame is None or ctx.deps.run_frame.model is None:
        raise RuntimeError("Subagent tool requires a populated parent runtime frame")

    spec = EphemeralSubagentSpec(
        name=name,
        role=role,
        capability=capability,
        task=task,
        parent_session_id="root",
        parent_run_id="root-run",
    )
    if ctx.deps.session_scope.parent_session_id is not None:
        raise RuntimeError("Root session scope cannot carry parent lineage")

    child_terminal: RunSucceededEvent | None = None
    preview_lines: list[str] = []
    async for event in stream_ephemeral_subagent_run_events(
        model=ctx.deps.run_frame.model,
        workspace_root=ctx.deps.workspace_root,
        spec=spec,
        current_date=ctx.deps.run_frame.current_date,
        shell_family=ctx.deps.shell_family,
        timezone=ctx.deps.run_frame.timezone,
        thinking=ctx.deps.run_frame.thinking,
    ):
        if event.type == "run_started":
            await _publish_subagent_update(
                ctx=ctx,
                name=name,
                role=role,
                summary="starting child run",
                preview_lines=preview_lines,
            )
        elif isinstance(event, ToolCallStartedEvent):
            preview_line = _preview_line_from_child_started_event(event)
            if preview_line is not None:
                preview_lines = _append_running_preview_line(
                    preview_lines,
                    preview_line,
                )
            await _publish_subagent_update(
                ctx=ctx,
                name=name,
                role=role,
                summary=_running_summary_for_role(role),
                preview_lines=preview_lines,
            )
        elif isinstance(event, RunSucceededEvent):
            child_terminal = event
        elif isinstance(event, RunFailedEvent):
            raise ToolOperationalError(
                f"Subagent {name} failed: {event.error_type}: {event.message}"
            )

    if child_terminal is None:
        raise RuntimeError("Subagent run ended without a terminal success event")

    output_text = _normalize_subagent_output_text(child_terminal.output_text)
    summary_text = _build_subagent_summary_text(output_text)
    return_value = SubagentToolResult(
        name=name,
        role=role,
        capability=capability,
        summary_text=summary_text,
        output_text=output_text,
    ).model_dump(mode="python")
    return make_tool_return(
        return_value=return_value,
        title=f"subagent {truncate_activity_label(name)}",
        display_label=_display_label_for_role(role),
        summary=_subagent_summary(summary_text),
        details=_subagent_details(
            name=name,
            role=role,
            preview_lines=_build_terminal_preview_lines(
                preview_lines,
                _subagent_summary(summary_text),
            ),
            preview_terminal=True,
        ),
    )


SUBAGENT_TOOL = Tool(
    subagent,
    takes_ctx=True,
    name="subagent",
    description=(
        "Run one ephemeral subagent for a bounded side task. Use it for "
        "focused investigation or verification, not broad multi-step work. "
        "The child uses the same workspace, model, and thinking, never gets "
        "write or edit, and returns one final report. Request capability="
        "'shell' when the child needs local commands or scripts beyond "
        "read, grep, find, and ls."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)

__all__ = ["SUBAGENT_TOOL", "SubagentToolResult", "subagent"]
