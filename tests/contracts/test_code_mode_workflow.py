from __future__ import annotations

import json
from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.run_events import (
    CodeModeActivityDetails,
    RunSucceededEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    ToolCallUpdatedEvent,
)
from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.code_mode import CodeModeCellContext
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import WorkspaceDeps


def _workflow_shell_command() -> str:
    if detect_default_shell_family() == "powershell":
        return "[Console]::Out.Write('workflow-ok')"
    return "printf workflow-ok"


def _has_exec_tool_return(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(message, ModelRequest)
        and any(
            isinstance(part, ToolReturnPart) and part.tool_name == "exec"
            for part in message.parts
        )
        for message in messages
    )


async def _code_mode_workflow_model(
    messages: list[ModelMessage],
    _agent_info,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _has_exec_tool_return(messages):
        yield "workflow complete"
        return

    yield {
        0: DeltaToolCall(
            name="exec",
            json_args=json.dumps(
                {
                    "source": "inspect recent jobs",
                    "yield_time_ms": 1000,
                    "max_output_tokens": 1000,
                }
            ),
            tool_call_id="call-exec",
        )
    }


async def test_code_mode_validates_jobs_tool_usage_workflow(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    jobs_dir = workspace_root / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "recent.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "tool_call_started",
                        "tool_name": "read",
                    }
                ),
                json.dumps(
                    {
                        "event": "tool_call_succeeded",
                        "tool_name": "read",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    async def code_mode_runner(ctx: CodeModeCellContext) -> str:
        raw_log = await ctx.tools.read(path="jobs/recent.jsonl")
        matches = await ctx.tools.grep(
            pattern="tool_call",
            path="jobs",
            literal=True,
        )
        shell_result = await ctx.tools.shell(command=_workflow_shell_command())
        ctx.emit(f"inspected {len(raw_log.splitlines())} lines")
        return json.dumps(
            {
                "has_tool_call_matches": "tool_call" in matches,
                "shell_output": shell_result["output"],
            },
            sort_keys=True,
        )

    deps = WorkspaceDeps.from_workspace_root(
        workspace_root,
        code_mode_runner=code_mode_runner,
    )
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=_code_mode_workflow_model),
        workspace_root=workspace_root,
        tool_names=[
            "read",
            "grep",
            "shell",
            "exec",
            "wait",
        ],
    )

    try:
        events = [
            event
            async for event in stream_run_events(
                agent=agent,
                prompt="inspect recent jobs",
                deps=deps,
                available_tool_names=["read", "grep", "shell", "exec", "wait"],
            )
        ]
    finally:
        await deps.read_only_worker.close()

    started = [event for event in events if isinstance(event, ToolCallStartedEvent)]
    assert [(event.tool_name, event.tool_call_id) for event in started] == [
        ("exec", "call-exec"),
    ]

    nested_updates = [
        event for event in events if isinstance(event, ToolCallUpdatedEvent)
    ]
    assert [
        (
            event.tool_name,
            event.activity.details.nested_tool,
            event.activity.details.nested_status,
        )
        for event in nested_updates
        if isinstance(event.activity.details, CodeModeActivityDetails)
    ] == [
        ("exec", "read", "started"),
        ("exec", "read", "succeeded"),
        ("exec", "grep", "started"),
        ("exec", "grep", "succeeded"),
        ("exec", "shell", "started"),
        ("exec", "shell", "succeeded"),
    ]

    succeeded = [
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    ]
    assert [(event.tool_name, event.tool_call_id) for event in succeeded] == [
        ("exec", "call-exec"),
    ]
    exec_result = succeeded[0].result
    assert exec_result["state"] == "completed"
    assert json.loads(exec_result["output"][-1]["text"]) == {
        "has_tool_call_matches": True,
        "shell_output": "workflow-ok",
    }

    assert isinstance(events[-1], RunSucceededEvent)
    assert events[-1].output_text == "workflow complete"
