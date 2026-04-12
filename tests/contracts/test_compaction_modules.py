import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.runtime.compaction import (
    build_resume_message_history,
    resume,
    session_summary,
    should_auto_compact_session,
    summarize_and_append_compaction_to_session,
    summarize_session_for_compaction,
    trigger,
)
from just_another_coding_agent.runtime.compaction.budget import (
    build_effective_compaction_context_window_tokens,
)
from just_another_coding_agent.session import (
    append_compaction_to_session,
    append_run_to_session,
    replacement_history,
    load_session,
)
from just_another_coding_agent.session.replacement_history import (
    build_compaction_summary_message,
)


def test_compaction_public_api_is_split_across_submodules() -> None:
    assert resume.build_resume_message_history is build_resume_message_history

    assert (
        session_summary.summarize_session_for_compaction
        is summarize_session_for_compaction
    )
    assert (
        session_summary.summarize_and_append_compaction_to_session
        is summarize_and_append_compaction_to_session
    )
    assert session_summary.should_auto_compact_session is should_auto_compact_session


def test_summarize_compaction_source_is_exported_through_package() -> None:
    from just_another_coding_agent.runtime.compaction import summarize_compaction_source

    assert (
        session_summary.summarize_compaction_source
        is summarize_compaction_source
    )


@pytest.mark.anyio
async def test_summarize_session_delegates_through_summarize_compaction_source(
    monkeypatch,
) -> None:
    captured_source: list[str] = []

    async def fake_summarize_compaction_source(*, model, source_text):
        captured_source.append(source_text)
        return "Primary Intent:\n- test intent"

    monkeypatch.setattr(
        session_summary,
        "summarize_compaction_source",
        fake_summarize_compaction_source,
    )

    loaded_session = SimpleNamespace(
        runs=[SimpleNamespace(run_id="run-1")],
    )

    monkeypatch.setattr(
        session_summary,
        "_build_compaction_source",
        lambda loaded, *, model: "fake source text",
    )

    result = await session_summary.summarize_session_for_compaction(
        model="test:model",
        loaded_session=loaded_session,
    )

    assert result == "Primary Intent:\n- test intent"
    assert captured_source == ["fake source text"]


def test_compaction_summary_instructions_focus_on_supported_sections() -> None:
    instructions = session_summary.COMPACTION_SUMMARY_INSTRUCTIONS

    assert "Primary Intent:" in instructions
    assert "Completed Work:" in instructions
    assert "Important Files/Paths:" in instructions
    assert "Failures / Open Issues:" in instructions
    assert "Current State:" in instructions
    assert "Next Step:" in instructions
    assert "Stable Preferences:" in instructions
    assert "Do not include code snippets" in instructions
    assert "Omit any section that has no concrete evidence." in instructions
    assert "Watch for bloat and rot" in instructions


def test_replacement_history_imports_cleanly_in_fresh_python_process() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import just_another_coding_agent.session.replacement_history",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_estimate_resume_history_budget_components_use_replacement_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setattr(
        trigger,
        "build_resume_message_history",
        lambda _loaded_session: [
            ModelRequest(parts=[UserPromptPart(content="user")]),
            ModelResponse(parts=[TextPart(content="done")], model_name="test"),
        ],
    )

    estimate = trigger._estimate_resume_history_budget_components(
        SimpleNamespace(
            header=SimpleNamespace(
                workspace_root=str(workspace_root.resolve()),
                shell_family="posix",
            ),
            latest_turn_context=None,
            has_persisted_turn_context_history=False,
            latest_compaction=SimpleNamespace(
                replacement_messages=[
                    ModelRequest(parts=[UserPromptPart(content="user")]),
                    build_compaction_summary_message("summary"),
                ]
            )
        ),
        model="test:model",
    )

    assert estimate.estimation_method == "chars_per_token_v1"
    assert estimate.estimated_runtime_context_tokens > 0
    assert estimate.estimated_resume_message_tokens > 0
    assert estimate.estimated_replacement_messages_tokens > 0
    assert estimate.estimated_replacement_summary_tokens > 0


def test_effective_compaction_context_window_reserves_output_headroom() -> None:
    assert build_effective_compaction_context_window_tokens(200_000) == 192_000
    assert build_effective_compaction_context_window_tokens(20_000) == 15_000


def test_should_auto_compact_session_uses_effective_context_window_budget(
    tmp_path,
    monkeypatch,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="only",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="only")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    def fake_budget_estimate(
        loaded_session,
        *,
        model,
        workspace_root=None,
        current_date=None,
        shell_family=None,
        thinking=None,
    ):
        del (
            loaded_session,
            model,
            workspace_root,
            current_date,
            shell_family,
            thinking,
        )
        return trigger._ResumeHistoryBudgetEstimate(
            estimation_method="chars_per_token_v1",
            estimated_runtime_context_tokens=500,
            estimated_resume_message_tokens=43_000,
            estimated_replacement_messages_tokens=0,
            estimated_replacement_summary_tokens=0,
        )

    monkeypatch.setattr(
        trigger,
        "_estimate_resume_history_budget_components",
        fake_budget_estimate,
    )

    assert trigger.should_auto_compact_session(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 100_000,
    )


def test_build_auto_compaction_budget_report_records_trigger_inputs(
    tmp_path,
    monkeypatch,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="only",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="only")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)

    def fake_budget_estimate(
        loaded_session,
        *,
        model,
        workspace_root=None,
        current_date=None,
        shell_family=None,
        thinking=None,
    ):
        del (
            loaded_session,
            model,
            workspace_root,
            current_date,
            shell_family,
            thinking,
        )
        return trigger._ResumeHistoryBudgetEstimate(
            estimation_method="chars_per_token_v1",
            estimated_runtime_context_tokens=500,
            estimated_resume_message_tokens=42_700,
            estimated_replacement_messages_tokens=900,
            estimated_replacement_summary_tokens=300,
        )

    monkeypatch.setattr(
        trigger,
        "_estimate_resume_history_budget_components",
        fake_budget_estimate,
    )

    report = trigger.build_auto_compact_session_budget_report(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 100_000,
    )

    assert report.should_compact is True
    assert report.reason == "over_budget"
    assert report.context_window_tokens == 100_000
    assert report.effective_context_window_tokens == 92_000
    assert report.output_headroom_tokens == 8_000
    assert report.trigger_budget_tokens == 64_400
    assert report.prompt_reserve_tokens == 24_000
    assert report.estimation_method == "chars_per_token_v1"
    assert report.estimated_runtime_context_tokens == 500
    assert report.estimated_resume_message_tokens == 42_700
    assert report.estimated_replacement_messages_tokens == 900
    assert report.estimated_replacement_summary_tokens == 300
    assert report.estimated_post_compaction_headroom_tokens == 24_800
    assert report.runs_since_latest_compaction == 1


def test_build_resume_message_history_uses_replacement_messages(tmp_path) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="first")])],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        replacement_messages=[
            ModelRequest(parts=[UserPromptPart(content="first")]),
            build_compaction_summary_message("summary"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[ModelRequest(parts=[UserPromptPart(content="second")])],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="done"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    resume_history = build_resume_message_history(loaded)

    assert [
        part.content
        for message in resume_history
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ] == [
        "first",
        "second",
    ]
    assert [
        part.content
        for message in resume_history
        for part in message.parts
        if isinstance(part, TextPart)
    ] == [build_compaction_summary_message("summary").parts[0].content]


def test_build_auto_compaction_budget_report_explains_no_new_work(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    large_prompt = "z" * 400_000

    for run_id in ["run-1", "run-2"]:
        append_run_to_session(
            path=session_path,
            workspace_root=workspace_root,
            prompt=large_prompt,
            thinking=None,
            messages=[ModelRequest(parts=[UserPromptPart(content=large_prompt)])],
            events=[
                RunStartedEvent(run_id=run_id),
                RunSucceededEvent(run_id=run_id, output_text="done"),
            ],
        )

    append_compaction_to_session(
        path=session_path,
        workspace_root=workspace_root,
        compacted_through_run_id="run-2",
        replacement_messages=[build_compaction_summary_message("summary")],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    report = trigger.build_auto_compact_session_budget_report(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 100_000,
    )

    assert report.should_compact is False
    assert report.reason == "no_new_work"
    assert report.runs_since_latest_compaction == 0


def test_strip_unpaired_tool_parts_passes_through_paired_messages() -> None:
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="bash", args="ls", tool_call_id="c1")],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="bash", content="ok", tool_call_id="c1")]
        ),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 2
    assert result[0].parts[0].tool_call_id == "c1"
    assert result[1].parts[0].tool_call_id == "c1"


def test_strip_unpaired_tool_parts_removes_orphaned_call() -> None:
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="bash", args="ls", tool_call_id="c1")],
            model_name="test",
        ),
        ModelRequest(parts=[UserPromptPart(content="hello")]),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 1
    assert isinstance(result[0], ModelRequest)


def test_strip_unpaired_tool_parts_removes_orphaned_return() -> None:
    messages = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="bash", content="ok", tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart(content="done")], model_name="test"),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 1
    assert isinstance(result[0], ModelResponse)


def test_strip_unpaired_tool_parts_handles_mixed_paired_and_orphaned() -> None:
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="bash", args="ls", tool_call_id="c1"),
                ToolCallPart(tool_name="read", args="f.py", tool_call_id="c2"),
            ],
            model_name="test",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="bash", content="ok", tool_call_id="c1"),
            ]
        ),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 2
    assert len(result[0].parts) == 1
    assert result[0].parts[0].tool_call_id == "c1"
    assert result[1].parts[0].tool_call_id == "c1"


def test_strip_unpaired_tool_parts_drops_empty_messages() -> None:
    messages = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="bash", content="ok", tool_call_id="orphan")]
        ),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 0


def test_check_in_run_compaction_needed_triggers_over_budget() -> None:
    large_content = "x" * 400_000
    messages = [ModelRequest(parts=[UserPromptPart(content=large_content)])]
    assert trigger.check_in_run_compaction_needed(
        messages,
        model="test:model",
        get_context_window_tokens=lambda _: 100_000,
    )


def test_check_in_run_compaction_needed_within_budget() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(content="short")])]
    assert not trigger.check_in_run_compaction_needed(
        messages,
        model="test:model",
        get_context_window_tokens=lambda _: 100_000,
    )


def test_check_in_run_compaction_needed_unknown_context_window() -> None:
    large_content = "x" * 400_000
    messages = [ModelRequest(parts=[UserPromptPart(content=large_content)])]
    assert not trigger.check_in_run_compaction_needed(
        messages,
        model="test:model",
        get_context_window_tokens=lambda _: None,
    )


def test_truncate_middle_returns_text_unchanged_when_within_budget() -> None:
    text = "hello world"
    result = replacement_history.truncate_middle_to_token_budget(text, 100)
    assert result == text


def test_truncate_middle_returns_none_for_zero_budget() -> None:
    assert replacement_history.truncate_middle_to_token_budget("hello", 0) is None


def test_truncate_middle_preserves_head_and_tail() -> None:
    head = "HEAD-" * 100
    middle = "MIDDLE-" * 1000
    tail = "TAIL-" * 100
    text = head + middle + tail

    result = replacement_history.truncate_middle_to_token_budget(text, 500)
    assert result is not None
    assert result.startswith("HEAD-")
    assert result.endswith("TAIL-")
    assert "tokens truncated" in result


def test_truncate_middle_marker_reports_approximate_removed_tokens() -> None:
    text = "x" * 40_000
    result = replacement_history.truncate_middle_to_token_budget(text, 1000)
    assert result is not None
    assert "tokens truncated" in result
    assert len(result) < len(text)


def test_truncate_middle_used_by_select_recent_user_message_tail() -> None:
    large_message = "A" * 200_000
    messages = [ModelRequest(parts=[UserPromptPart(content=large_message)])]
    result = replacement_history.build_compaction_replacement_messages(
        model="test:model",
        messages=messages,
        summary_text="summary",
        token_budget=1000,
    )
    user_msgs = [
        m for m in result
        if isinstance(m, ModelRequest)
        and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    assert len(user_msgs) == 1
    content = user_msgs[0].parts[0].content
    assert "tokens truncated" in content


@pytest.mark.anyio
async def test_in_run_compaction_fires_during_tool_loop(monkeypatch) -> None:
    from collections.abc import AsyncIterator

    from pydantic_ai import Agent
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    call_count = 0

    async def tool_loop_stream(
        messages: object,
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 5:
            yield {
                0: DeltaToolCall(
                    name="tick",
                    json_args="{}",
                    tool_call_id=f"call-{call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(
        FunctionModel(stream_function=tool_loop_stream), output_type=str
    )

    @agent.tool_plain
    async def tick():
        return "ok"

    compaction_triggered = False

    def fake_check(messages, *, model):
        nonlocal compaction_triggered
        if not compaction_triggered:
            compaction_triggered = True
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    async def fake_summarize(*, model, source_text):
        return "Primary Intent:\n- testing in-run compaction"

    monkeypatch.setattr(run_module, "summarize_compaction_source", fake_summarize)

    async def noop_activate(callback):
        pass

    async def noop_submit():
        pass

    async def noop_deactivate():
        pass

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=["tick"],
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    event_types = [type(e).__name__ for e in events]
    assert event_types[0] == "RunStartedEvent"
    assert event_types[-1] == "RunSucceededEvent", f"Got: {events[-1]}"
    assert compaction_triggered

    from just_another_coding_agent.contracts.run_events import (
        InRunCompactionCompletedEvent,
    )

    compaction_events = [
        e for e in events if isinstance(e, InRunCompactionCompletedEvent)
    ]
    assert len(compaction_events) == 1
    assert compaction_events[0].live_message_count > 0
    assert compaction_events[0].replacement_message_count > 0


@pytest.mark.anyio
async def test_in_run_compaction_circuit_breaker_on_failure(monkeypatch) -> None:
    from collections.abc import AsyncIterator

    from pydantic_ai import Agent
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    call_count = 0

    async def tool_loop_stream(
        messages: object,
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 8:
            yield {
                0: DeltaToolCall(
                    name="tick",
                    json_args="{}",
                    tool_call_id=f"call-{call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(
        FunctionModel(stream_function=tool_loop_stream), output_type=str
    )

    @agent.tool_plain
    async def tick():
        return "ok"

    check_count = 0

    def fake_check(messages, *, model):
        nonlocal check_count
        check_count += 1
        return True

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    async def failing_summarize(*, model, source_text):
        raise RuntimeError("summarizer broke")

    monkeypatch.setattr(run_module, "summarize_compaction_source", failing_summarize)

    async def noop_activate(callback):
        pass

    async def noop_submit():
        pass

    async def noop_deactivate():
        pass

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=["tick"],
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    event_types = [type(e).__name__ for e in events]
    assert event_types[0] == "RunStartedEvent"
    assert event_types[-1] == "RunSucceededEvent", f"Got: {events[-1]}"
    assert check_count == 3


@pytest.mark.anyio
async def test_in_run_compaction_message_history_sink_fires_once(
    monkeypatch,
) -> None:
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    call_count = 0

    async def tool_loop_stream(
        messages: object,
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 5:
            yield {
                0: DeltaToolCall(
                    name="tick",
                    json_args="{}",
                    tool_call_id=f"call-{call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(
        FunctionModel(stream_function=tool_loop_stream), output_type=str
    )

    @agent.tool_plain
    async def tick():
        return "ok"

    compaction_triggered = False

    def fake_check(messages, *, model):
        nonlocal compaction_triggered
        if not compaction_triggered:
            compaction_triggered = True
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    async def fake_summarize(*, model, source_text):
        return "Primary Intent:\n- testing sink"

    monkeypatch.setattr(run_module, "summarize_compaction_source", fake_summarize)

    sink_calls: list[Sequence[ModelMessage]] = []

    def recording_sink(messages: Sequence[ModelMessage]) -> None:
        sink_calls.append(messages)

    async def noop_activate(callback):
        pass

    async def noop_submit():
        pass

    async def noop_deactivate():
        pass

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=["tick"],
            message_history_sink=recording_sink,
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    assert events[-1].type == "run_succeeded"
    assert len(sink_calls) == 1


@pytest.mark.anyio
async def test_in_run_compaction_hysteresis_suppresses_immediate_recheck(
    monkeypatch,
) -> None:
    from collections.abc import AsyncIterator

    from pydantic_ai import Agent
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    call_count = 0

    async def tool_loop_stream(
        messages: object,
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 8:
            yield {
                0: DeltaToolCall(
                    name="tick",
                    json_args="{}",
                    tool_call_id=f"call-{call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(
        FunctionModel(stream_function=tool_loop_stream), output_type=str
    )

    @agent.tool_plain
    async def tick():
        return "ok"

    check_call_count = 0

    def counting_check(messages, *, model):
        nonlocal check_call_count
        check_call_count += 1
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", counting_check)

    async def noop_activate(callback):
        pass

    async def noop_submit():
        pass

    async def noop_deactivate():
        pass

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            available_tool_names=["tick"],
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    assert events[-1].type == "run_succeeded"
    from just_another_coding_agent.runtime.run import MIN_TOOL_RESULTS_BETWEEN_COMPACTIONS
    assert check_call_count <= call_count - MIN_TOOL_RESULTS_BETWEEN_COMPACTIONS + 1


def test_strip_unpaired_tool_parts_after_tail_selection_drops_orphaned_return() -> None:
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="bash", args="ls", tool_call_id="c1")],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="bash", content="ok", tool_call_id="c1")]
        ),
        ModelResponse(
            parts=[ToolCallPart(tool_name="read", args="f.py", tool_call_id="c2")],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="read", content="data", tool_call_id="c2")]
        ),
    ]
    tail_only = messages[2:]
    result = replacement_history.strip_unpaired_tool_parts(tail_only)
    for msg in result:
        for part in msg.parts:
            if isinstance(part, (ToolCallPart, ToolReturnPart)):
                call_ids = {
                    p.tool_call_id
                    for m in result
                    for p in m.parts
                    if isinstance(p, ToolCallPart)
                }
                return_ids = {
                    p.tool_call_id
                    for m in result
                    for p in m.parts
                    if isinstance(p, ToolReturnPart)
                }
                assert call_ids == return_ids
