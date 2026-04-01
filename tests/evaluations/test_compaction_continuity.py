from collections.abc import AsyncIterator

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from just_another_coding_agent.contracts.run_events import (
    ReadActivityDetails,
    RunStartedEvent,
    RunSucceededEvent,
    ShellActivityDetails,
    ToolActivity,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
    ToolCallSucceededEvent,
    WriteActivityDetails,
)
from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.runtime.compaction import (
    summarize_session_for_compaction,
)
from just_another_coding_agent.session import (
    append_compaction_to_session,
    append_run_to_session,
    load_session,
)


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        for part in message.parts:
            yield part


def _system_prompt_contents(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, SystemPromptPart)
    ]


def _test_summary_model(*, custom_output_args: dict[str, object]) -> TestModel:
    return TestModel(call_tools=[], custom_output_args=custom_output_args)


async def test_auto_compaction_preserves_multi_compaction_continuity(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    large_prompt = "x" * 180_000

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt=f"inspect plan {large_prompt}",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content=f"inspect plan {large_prompt}")])
        ],
        events=[
            RunStartedEvent(run_id="run-1"),
            ToolCallStartedEvent(
                run_id="run-1",
                tool_call_id="call-read-plan",
                tool_name="read",
                args={"path": "docs/plan.md"},
                args_valid=True,
            ),
            ToolCallSucceededEvent(
                run_id="run-1",
                tool_call_id="call-read-plan",
                tool_name="read",
                result="read result",
                activity=ToolActivity(
                    title="Read docs/plan.md",
                    details=ReadActivityDetails(
                        path=str(workspace_root / "docs/plan.md"),
                        short_path="docs/plan.md",
                        offset=1,
                        limit=200,
                    ),
                ),
            ),
            RunSucceededEvent(run_id="run-1", output_text="plan reviewed"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt=f"run verifier {large_prompt}",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content=f"run verifier {large_prompt}")])
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            ToolCallStartedEvent(
                run_id="run-2",
                tool_call_id="call-pytest",
                tool_name="shell",
                args={"command": "pytest -q"},
                args_valid=True,
            ),
            ToolCallFailedEvent(
                run_id="run-2",
                tool_call_id="call-pytest",
                tool_name="shell",
                error_type="ToolCommandError",
                message="Command exited with code 1",
                activity=ToolActivity(
                    title="shell pytest -q",
                    summary="Command exited with code 1",
                ),
            ),
            RunSucceededEvent(run_id="run-2", output_text="pytest failed"),
        ],
    )

    first_loaded = load_session(path=session_path, workspace_root=workspace_root)
    first_summary = await summarize_session_for_compaction(
        model=_test_summary_model(
            custom_output_args={
                "current_objective": "repair the failing verifier",
                "established_facts": ["The project plan was reviewed."],
                "user_preferences": ["be concise"],
                "important_paths": ["docs/plan.md", "src/app.py"],
                "open_questions": [],
                "unresolved_work": ["Patch src/app.py."],
            }
        ),
        loaded_session=first_loaded,
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        summary=first_summary,
    )

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt=f"patch app {large_prompt}",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content=f"patch app {large_prompt}")])
        ],
        events=[
            RunStartedEvent(run_id="run-3"),
            ToolCallStartedEvent(
                run_id="run-3",
                tool_call_id="call-read-app",
                tool_name="read",
                args={"path": "src/app.py"},
                args_valid=True,
            ),
            ToolCallSucceededEvent(
                run_id="run-3",
                tool_call_id="call-read-app",
                tool_name="read",
                result="read result",
                activity=ToolActivity(
                    title="Read src/app.py",
                    details=ReadActivityDetails(
                        path=str(workspace_root / "src/app.py"),
                        short_path="src/app.py",
                        offset=1,
                        limit=200,
                    ),
                ),
            ),
            ToolCallStartedEvent(
                run_id="run-3",
                tool_call_id="call-write-app",
                tool_name="write",
                args={"path": "src/app.py", "content": "print('ok')\n"},
                args_valid=True,
            ),
            ToolCallSucceededEvent(
                run_id="run-3",
                tool_call_id="call-write-app",
                tool_name="write",
                result="write result",
                activity=ToolActivity(
                    title="Wrote src/app.py",
                    details=WriteActivityDetails(
                        path=str(workspace_root / "src/app.py"),
                        bytes_written=12,
                    ),
                ),
            ),
            RunSucceededEvent(run_id="run-3", output_text="patched app"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt=f"rerun go tests {large_prompt}",
        thinking=None,
        messages=[
            ModelRequest(
                parts=[UserPromptPart(content=f"rerun go tests {large_prompt}")]
            )
        ],
        events=[
            RunStartedEvent(run_id="run-4"),
            ToolCallStartedEvent(
                run_id="run-4",
                tool_call_id="call-go-test",
                tool_name="shell",
                args={"command": "go test ./..."},
                args_valid=True,
            ),
            ToolCallSucceededEvent(
                run_id="run-4",
                tool_call_id="call-go-test",
                tool_name="shell",
                result={"exit_code": 0, "output": "ok"},
                activity=ToolActivity(
                    title="shell go test ./...",
                    summary="command exited 0",
                    details=ShellActivityDetails(
                        command_preview="go test ./...",
                        shell_family="posix",
                        exit_code=0,
                    ),
                ),
            ),
            ToolCallStartedEvent(
                run_id="run-4",
                tool_call_id="call-read-test",
                tool_name="read",
                args={"path": "tests/test_app.py"},
                args_valid=True,
            ),
            ToolCallSucceededEvent(
                run_id="run-4",
                tool_call_id="call-read-test",
                tool_name="read",
                result="read result",
                activity=ToolActivity(
                    title="Read tests/test_app.py",
                    details=ReadActivityDetails(
                        path=str(workspace_root / "tests/test_app.py"),
                        short_path="tests/test_app.py",
                        offset=1,
                        limit=200,
                    ),
                ),
            ),
            RunSucceededEvent(run_id="run-4", output_text="go tests passed"),
        ],
    )

    async def fake_summarize_and_append_compaction_to_session(
        *,
        model,
        path,
        workspace_root,
    ):
        del model
        loaded = load_session(path=path, workspace_root=workspace_root)
        summary = await summarize_session_for_compaction(
            model=_test_summary_model(
                custom_output_args={
                    "current_objective": "ship the verified app fix",
                    "established_facts": [
                        "src/app.py was updated after the earlier verifier failure.",
                        "go test ./... passed on the latest run.",
                    ],
                    "user_preferences": ["be concise"],
                    "important_paths": ["src/app.py", "tests/test_app.py"],
                    "open_questions": [],
                    "unresolved_work": ["Send the user a final status update."],
                }
            ),
            loaded_session=loaded,
        )
        return append_compaction_to_session(
            path=path,
            workspace_root=workspace_root,
            summary=summary,
        )

    async def continuity_probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        user_prompts = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, UserPromptPart)
        ]
        system_prompts = _system_prompt_contents(messages)

        assert user_prompts == ["what should we do next?"]
        assert len(system_prompts) >= 1

        summary_prompt = next(
            prompt
            for prompt in system_prompts
            if prompt.startswith("Session compaction summary:")
        )
        assert "Current objective: ship the verified app fix" in summary_prompt
        assert "Established facts:" in summary_prompt
        assert (
            "- src/app.py was updated after the earlier verifier failure."
            in summary_prompt
        )
        assert "- go test ./... passed on the latest run." in summary_prompt
        assert "User preferences:" in summary_prompt
        assert "- be concise" in summary_prompt
        assert "Important paths:" in summary_prompt
        assert "- src/app.py" in summary_prompt
        assert "- tests/test_app.py" in summary_prompt
        assert "Read paths:" in summary_prompt
        assert "- docs/plan.md" in summary_prompt
        assert "- src/app.py" in summary_prompt
        assert "- tests/test_app.py" in summary_prompt
        assert "Modified paths:" in summary_prompt
        assert "- src/app.py" in summary_prompt
        assert "Recent shell commands:" in summary_prompt
        assert "- pytest -q (failed)" in summary_prompt
        assert "- go test ./... (exit 0)" in summary_prompt
        assert "Recent failures:" in summary_prompt
        assert (
            "- shell pytest -q failed: Command exited with code 1"
            in summary_prompt
        )
        assert "Unresolved work:" in summary_prompt
        assert "- Send the user a final status update." in summary_prompt

        yield (
            "We fixed src/app.py, the latest go test ./... passed, and the "
            "earlier pytest -q failure is captured in session continuity."
        )

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.session.summarize_and_append_compaction_to_session",
        fake_summarize_and_append_compaction_to_session,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.compaction.session_summary.get_model_context_window_tokens",
        lambda _model: 100_000,
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=continuity_probe_stream),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="what should we do next?",
        )
    ]

    assert [event.type for event in events] == [
        "session_compaction_started",
        "session_compaction_completed",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert loaded.latest_compaction.summarized_through_run_id == "run-4"
    assert loaded.latest_compaction.summary.read_paths == [
        "docs/plan.md",
        "src/app.py",
        "tests/test_app.py",
    ]
    assert loaded.latest_compaction.summary.modified_paths == ["src/app.py"]
    assert loaded.latest_compaction.summary.recent_shell_commands == [
        "pytest -q (failed)",
        "go test ./... (exit 0)",
    ]
    assert loaded.latest_compaction.summary.recent_failures == [
        "shell pytest -q failed: Command exited with code 1"
    ]
