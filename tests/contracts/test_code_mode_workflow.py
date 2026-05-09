from __future__ import annotations

import json
from collections.abc import AsyncIterator

from pydantic_ai import capture_run_messages
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
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import WorkspaceDeps


def _workflow_shell_command() -> str:
    if detect_default_shell_family() == "powershell":
        return "[Console]::Out.Write('workflow-ok')"
    return "printf workflow-ok"


def _failing_shell_command() -> str:
    if detect_default_shell_family() == "powershell":
        return "Write-Output workflow-fail; exit 7"
    return "printf workflow-fail; exit 7"


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
                    "source": (
                        "raw_log = await tools.read(path='jobs/recent.jsonl')\n"
                        "matches = await tools.grep("
                        "pattern='tool_call', path='jobs', literal=True)\n"
                        "shell_result = await tools.shell("
                        f"command={_workflow_shell_command()!r})\n"
                        "emit(f'inspected {len(raw_log.splitlines())} lines')\n"
                        "return_result(json.dumps({\n"
                        "    'has_tool_call_matches': 'tool_call' in matches,\n"
                        "    'shell_output': shell_result['output'],\n"
                        "}, sort_keys=True))"
                    ),
                    "yield_time_ms": 1000,
                    "max_output_tokens": 1000,
                }
            ),
            tool_call_id="call-exec",
        )
    }


async def _code_mode_failure_model(
    messages: list[ModelMessage],
    _agent_info,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if _has_exec_tool_return(messages):
        yield "failure inspected"
        return

    yield {
        0: DeltaToolCall(
            name="exec",
            json_args=json.dumps(
                {
                    "source": (
                        "await tools.shell("
                        f"command={_failing_shell_command()!r})\n"
                        "return_result('unreachable')"
                    ),
                    "yield_time_ms": 5000,
                    "max_output_tokens": 1000,
                }
            ),
            tool_call_id="call-exec-fails",
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

    deps = WorkspaceDeps.from_workspace_root(workspace_root)
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
        with capture_run_messages() as messages:
            events = [
                event
                async for event in stream_run_events(
                    agent=agent,
                    prompt="inspect recent jobs",
                    deps=deps,
                    available_tool_names=[
                        "read",
                        "grep",
                        "shell",
                        "exec",
                        "wait",
                    ],
                )
            ]
    finally:
        await deps.close_runtime_resources()

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

    tool_return_names = [
        part.tool_name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert tool_return_names == ["exec"]


async def test_code_mode_nested_failure_stays_under_parent_exec(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    deps = WorkspaceDeps.from_workspace_root(workspace_root)
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=_code_mode_failure_model),
        workspace_root=workspace_root,
        tool_names=[
            "shell",
            "exec",
            "wait",
        ],
    )

    try:
        with capture_run_messages() as messages:
            events = [
                event
                async for event in stream_run_events(
                    agent=agent,
                    prompt="inspect failing code mode",
                    deps=deps,
                    available_tool_names=["shell", "exec", "wait"],
                )
            ]
    finally:
        await deps.close_runtime_resources()

    started = [event for event in events if isinstance(event, ToolCallStartedEvent)]
    assert [(event.tool_name, event.tool_call_id) for event in started] == [
        ("exec", "call-exec-fails"),
    ]

    nested_statuses = [
        (
            event.tool_name,
            event.activity.details.nested_tool,
            event.activity.details.nested_status,
        )
        for event in events
        if isinstance(event, ToolCallUpdatedEvent)
        and isinstance(event.activity.details, CodeModeActivityDetails)
    ]
    assert nested_statuses == [
        ("exec", "shell", "started"),
        ("exec", "shell", "failed"),
    ]

    succeeded = [
        event for event in events if isinstance(event, ToolCallSucceededEvent)
    ]
    assert [(event.tool_name, event.tool_call_id) for event in succeeded] == [
        ("exec", "call-exec-fails"),
    ]
    exec_result = succeeded[0].result
    assert exec_result["state"] == "failed"
    assert exec_result["error"]["error_type"] == "CodeModeSourceRuntimeError"
    assert "ToolCommandError" in exec_result["error"]["message"]

    assert isinstance(events[-1], RunSucceededEvent)
    assert events[-1].output_text == "failure inspected"

    tool_return_names = [
        part.tool_name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert tool_return_names == ["exec"]
