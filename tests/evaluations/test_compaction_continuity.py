from collections.abc import AsyncIterator

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
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
    build_resume_message_history,
    summarize_session_for_compaction,
)
from just_another_coding_agent.session import (
    append_compaction_to_session,
    append_run_to_session,
    load_session,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_replacement_messages,
    build_compaction_summary_message,
    extract_compaction_summary_text,
)


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        for part in message.parts:
            yield part


def _assistant_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, TextPart)
    ]


def _test_summary_model(*, custom_output_text: str) -> TestModel:
    return TestModel(call_tools=[], custom_output_text=custom_output_text)


def _append_summary_compaction(
    *,
    path,
    workspace_root,
    summary_text: str,
    token_budget: int = 400,
):
    loaded = load_session(path=path, workspace_root=workspace_root)
    replacement_messages = build_compaction_replacement_messages(
        model="test:model",
        messages=build_resume_message_history(loaded),
        summary_text=summary_text,
        token_budget=token_budget,
    )
    return append_compaction_to_session(
        path=path,
        workspace_root=workspace_root,
        replacement_messages=replacement_messages,
    )


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
            custom_output_text="\n".join(
                [
                    "- Goal: repair the failing verifier",
                    "- Established fact: The project plan was reviewed.",
                    "- Important path: docs/plan.md",
                    "- Unresolved work: Patch src/app.py.",
                ]
            )
        ),
        loaded_session=first_loaded,
    )
    _append_summary_compaction(
        path=session_path,
        workspace_root=workspace_root,
        summary_text=first_summary,
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
        second_summary = await summarize_session_for_compaction(
            model=FunctionModel(function=_summary_probe_function),
            loaded_session=loaded,
        )
        return _append_summary_compaction(
            path=path,
            workspace_root=workspace_root,
            summary_text=second_summary,
        )

    observed: dict[str, list[str]] = {}

    async def continuity_probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["user_prompts"] = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, UserPromptPart)
        ]
        observed["assistant_texts"] = _assistant_texts(messages)
        assert observed["user_prompts"][-1] == "what should we do next?"
        assert any(
            "ship the verified app fix" in text for text in observed["assistant_texts"]
        )
        yield "send the user the verified app update"

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
        "session_compaction_warning",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert events[2].compaction_count == 2
    assert events[2].message == (
        "Session has been compacted multiple times; continuity quality may "
        "degrade."
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert extract_compaction_summary_text(loaded.latest_compaction.replacement_messages) == (
        "\n".join(
            [
                "- Goal: ship the verified app fix",
                "- Established fact: src/app.py was updated after the earlier verifier failure.",
                "- Established fact: go test ./... passed on the latest run.",
                "- Important path: src/app.py",
                "- Important path: tests/test_app.py",
                "- Unresolved work: Send the user a final status update.",
            ]
        )
    )
    assert build_compaction_summary_message(
        "\n".join(
            [
                "- Goal: ship the verified app fix",
                "- Established fact: src/app.py was updated after the earlier verifier failure.",
                "- Established fact: go test ./... passed on the latest run.",
                "- Important path: src/app.py",
                "- Important path: tests/test_app.py",
                "- Unresolved work: Send the user a final status update.",
            ]
        )
    ).parts[0].content in observed["assistant_texts"]


def _summary_probe_function(
    messages: list[ModelMessage],
    _agent_info: object,
) -> ModelResponse:
    prompt = next(
        part.content
        for part in _all_parts(messages)
        if isinstance(part, UserPromptPart)
    )
    assert "Previous compaction summary:" in prompt
    assert "- Goal: repair the failing verifier" in prompt
    assert "Run run-3" in prompt
    assert "Run run-4" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content="\n".join(
                    [
                        "- Goal: ship the verified app fix",
                        "- Established fact: src/app.py was updated after the earlier verifier failure.",
                        "- Established fact: go test ./... passed on the latest run.",
                        "- Important path: src/app.py",
                        "- Important path: tests/test_app.py",
                        "- Unresolved work: Send the user a final status update.",
                    ]
                )
            )
        ]
    )
