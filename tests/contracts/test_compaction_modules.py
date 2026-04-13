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
    load_session,
    replacement_history,
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

    assert session_summary.summarize_compaction_source is summarize_compaction_source


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
            ),
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
            parts=[
                ToolReturnPart(
                    tool_name="bash",
                    content="ok",
                    tool_call_id="orphan",
                )
            ]
        ),
    ]
    result = replacement_history.strip_unpaired_tool_parts(messages)
    assert len(result) == 0


def test_check_in_run_compaction_needed_triggers_over_budget() -> None:
    large_content = "hello world! " * 40_000
    messages = [ModelRequest(parts=[UserPromptPart(content=large_content)])]
    assert trigger.check_in_run_compaction_needed(
        messages,
        model="test:model",
        get_context_window_tokens=lambda _: 100_000,
    )


def test_check_in_run_compaction_needed_counts_pending_prompt() -> None:
    # Small history alone would not trigger compaction, but adding a huge
    # pending_prompt (the next turn's user message) should push it over the
    # threshold. This mirrors the runtime wiring where agent.iter() is called
    # with a separate prompt argument that becomes a new UserPromptPart.
    small_messages = [ModelRequest(parts=[UserPromptPart(content="short")])]
    large_prompt = "hello world! " * 40_000

    assert not trigger.check_in_run_compaction_needed(
        small_messages,
        model="test:model",
        get_context_window_tokens=lambda _: 100_000,
    )
    assert trigger.check_in_run_compaction_needed(
        small_messages,
        model="test:model",
        pending_prompt=large_prompt,
        get_context_window_tokens=lambda _: 100_000,
    )


def test_check_in_run_compaction_needed_pending_prompt_distinct_from_history() -> None:
    # A pending prompt with the same text as a prior history message should
    # still be counted (it's a new turn). The caller is responsible for
    # passing pending_prompt only on the FIRST check of each agent.iter block
    # (run.py uses len(captured_messages) == attempt_history_count as the
    # signal). This test verifies the token-counting side: same-text history
    # and pending prompt are NOT conflated.
    prompt_text = "repeat"
    messages = [ModelRequest(parts=[UserPromptPart(content=prompt_text)])]

    without_pp = trigger.estimate_next_request_input_tokens(
        messages, model="test:model"
    )
    with_pp = trigger.estimate_next_request_input_tokens(
        messages, model="test:model", pending_prompt=prompt_text
    )
    assert with_pp > without_pp


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
        m
        for m in result
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

    agent = Agent(FunctionModel(stream_function=tool_loop_stream), output_type=str)

    @agent.tool_plain
    async def tick():
        return "ok"

    compaction_triggered = False

    def fake_check(messages, *, model, last_response_usage=None, pending_prompt=None):
        nonlocal compaction_triggered
        has_tool_return = any(
            isinstance(p, ToolReturnPart)
            for m in messages
            for p in getattr(m, "parts", [])
        )
        if has_tool_return and not compaction_triggered:
            compaction_triggered = True
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

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
async def test_in_run_compaction_does_not_require_tool_activity(
    monkeypatch,
) -> None:
    from collections.abc import AsyncIterator

    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    stream_call_count = 0
    check_call_count = 0

    async def single_response_stream(
        messages: object,
        _agent_info: object,
    ) -> AsyncIterator[str]:
        del messages, _agent_info
        nonlocal stream_call_count
        stream_call_count += 1
        yield "done"

    agent = Agent(
        FunctionModel(stream_function=single_response_stream), output_type=str
    )

    def fake_check(messages, *, model, last_response_usage=None, pending_prompt=None):
        del messages, model
        nonlocal check_call_count
        check_call_count += 1
        return check_call_count == 1

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    async def noop_activate(callback):
        del callback

    async def noop_submit():
        return None

    async def noop_deactivate():
        return None

    seed_history = [
        ModelRequest(parts=[UserPromptPart(content="earlier user turn")]),
        ModelResponse(
            parts=[TextPart(content="earlier assistant text")], model_name="x"
        ),
    ]
    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            message_history=seed_history,
            available_tool_names=[],
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    from just_another_coding_agent.contracts.run_events import (
        InRunCompactionCompletedEvent,
    )

    compaction_events = [
        event for event in events if isinstance(event, InRunCompactionCompletedEvent)
    ]
    assert events[-1].type == "run_succeeded"
    assert len(compaction_events) == 1
    assert stream_call_count == 1
    assert check_call_count >= 2


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

    agent = Agent(FunctionModel(stream_function=tool_loop_stream), output_type=str)

    @agent.tool_plain
    async def tick():
        return "ok"

    check_count = 0

    def fake_check(messages, *, model, last_response_usage=None, pending_prompt=None):
        nonlocal check_count
        check_count += 1
        return True

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    def failing_build(*, messages, model, token_budget):
        raise RuntimeError("truncation broke")

    monkeypatch.setattr(run_module, "build_in_run_truncated_history", failing_build)

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

    agent = Agent(FunctionModel(stream_function=tool_loop_stream), output_type=str)

    @agent.tool_plain
    async def tick():
        return "ok"

    compaction_triggered = False

    def fake_check(messages, *, model, last_response_usage=None, pending_prompt=None):
        nonlocal compaction_triggered
        if not compaction_triggered:
            compaction_triggered = True
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

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
async def test_in_run_compaction_rechecks_next_request_without_tool_hysteresis(
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

    agent = Agent(FunctionModel(stream_function=tool_loop_stream), output_type=str)

    @agent.tool_plain
    async def tick():
        return "ok"

    check_call_count = 0

    def fake_check(messages, *, model, last_response_usage=None, pending_prompt=None):
        del messages, model
        nonlocal check_call_count
        check_call_count += 1
        return check_call_count == 1

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

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
    from just_another_coding_agent.contracts.run_events import (
        InRunCompactionCompletedEvent,
    )

    compaction_events = [
        e for e in events if isinstance(e, InRunCompactionCompletedEvent)
    ]
    assert len(compaction_events) == 1
    assert check_call_count >= 2


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


def test_build_in_run_truncated_history_keeps_user_prefix_and_recent_tail() -> None:
    from pydantic_ai.messages import ThinkingPart

    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="solve this task")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="", signature="opaque-1"),
                ToolCallPart(tool_name="read", args={"path": "a"}, tool_call_id="c1"),
            ],
            model_name="x",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="read", content="file a", tool_call_id="c1")
            ]
        ),
        ModelResponse(
            parts=[
                ThinkingPart(content="", signature="opaque-2"),
                TextPart(content="looking at b next"),
                ToolCallPart(tool_name="read", args={"path": "b"}, tool_call_id="c2"),
            ],
            model_name="x",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="read", content="file b", tool_call_id="c2")
            ]
        ),
    ]
    result = build_in_run_truncated_history(
        messages=messages, model="test:model", token_budget=100_000
    )
    # Prefix: user prompt kept
    assert isinstance(result[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in result[0].parts)
    # ThinkingPart stripped everywhere
    for m in result:
        assert not any(isinstance(p, ThinkingPart) for p in m.parts)
    # Tool pairing intact
    call_ids = {
        p.tool_call_id for m in result for p in m.parts if isinstance(p, ToolCallPart)
    }
    return_ids = {
        p.tool_call_id for m in result for p in m.parts if isinstance(p, ToolReturnPart)
    }
    assert call_ids == return_ids
    assert call_ids == {"c1", "c2"}


def test_build_in_run_truncated_history_drops_tail_under_tight_budget() -> None:
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="solve this task")]),
        ModelResponse(parts=[TextPart(content="a" * 10_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="b" * 10_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="c" * 10_000)], model_name="x"),
    ]
    result = build_in_run_truncated_history(
        messages=messages, model="test:model", token_budget=1_500
    )
    # Prefix kept, body truncated: should have fewer messages than input
    assert len(result) < len(messages)
    assert isinstance(result[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in result[0].parts)


def test_build_in_run_truncated_history_preserves_user_turn_not_at_leading_edge() -> (
    None
):
    # Simulates a history that's already been compacted once, so the leading
    # messages are rebuilt initial_context (ModelResponse project docs) rather
    # than a user prompt. The real user task sits in the middle of the list.
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    messages = [
        ModelResponse(
            parts=[TextPart(content="Project instructions for this workspace: ...")],
            model_name="jaca-project-docs",
        ),
        ModelResponse(
            parts=[TextPart(content="Runtime context for this turn: date=...")],
            model_name="jaca-runtime-context",
        ),
        ModelRequest(parts=[UserPromptPart(content="the original task description")]),
        ModelResponse(parts=[TextPart(content="working on it")], model_name="x"),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="read", args={"path": "a"}, tool_call_id="c1")
            ],
            model_name="x",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="read", content="A", tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart(content="x" * 20_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="y" * 20_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="z" * 20_000)], model_name="x"),
    ]
    result = build_in_run_truncated_history(
        messages=messages, model="test:model", token_budget=2_000
    )
    # The user prompt must be preserved even though it's not at the leading edge
    user_msgs = [
        m
        for m in result
        if isinstance(m, ModelRequest)
        and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].parts[0].content == "the original task description"


def test_build_in_run_truncated_history_preserves_prior_compaction_summary() -> None:
    # A resumed or multi-compacted history can start with a durable compaction
    # summary ModelResponse. That summary must survive subsequent in-run
    # compactions even when the budget forces tail truncation.
    from just_another_coding_agent.session.replacement_history import (
        build_compaction_summary_message,
        build_in_run_truncated_history,
    )

    summary = build_compaction_summary_message(
        "Primary Intent: previously summarized work"
    )
    messages = [
        summary,
        ModelRequest(parts=[UserPromptPart(content="continue the work")]),
        ModelResponse(parts=[TextPart(content="a" * 15_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="b" * 15_000)], model_name="x"),
        ModelResponse(parts=[TextPart(content="c" * 15_000)], model_name="x"),
    ]
    result = build_in_run_truncated_history(
        messages=messages, model="test:model", token_budget=1_500
    )
    # The prior summary must still be present in the result
    summary_found = False
    for m in result:
        if isinstance(m, ModelResponse):
            for p in m.parts:
                if (
                    isinstance(p, TextPart)
                    and "previously summarized work" in p.content
                ):
                    summary_found = True
    assert summary_found
    # And the user prompt must also be preserved
    user_msgs = [
        m
        for m in result
        if isinstance(m, ModelRequest)
        and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    assert len(user_msgs) == 1


def test_build_in_run_truncated_history_preserves_mid_run_user_steer() -> None:
    # When a user steers mid-run with a new prompt, that prompt motivates
    # subsequent tool calls. Keeping the recent tool rounds without the
    # steering user message leaves the model with evidence but no instruction.
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="initial task")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="read", args={"path": "a"}, tool_call_id="c1")
            ],
            model_name="x",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="read", content="A", tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart(content="padding " * 3000)], model_name="x"),
        ModelRequest(parts=[UserPromptPart(content="now please check file X")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="read", args={"path": "X"}, tool_call_id="c2")
            ],
            model_name="x",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="read", content="X content", tool_call_id="c2")
            ]
        ),
    ]
    result = build_in_run_truncated_history(
        messages=messages, model="test:model", token_budget=500
    )
    # Both user prompts must survive
    user_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    assert "initial task" in user_texts
    assert "now please check file X" in user_texts


def test_build_in_run_truncated_history_does_not_anchor_synthetic_prompts() -> None:
    # A synthetic continuation prompt must not be preserved as an anchor, so
    # it can be truncated away when budget is tight while the real user
    # prompt stays.
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    synthetic_text = "Continue the task. Earlier turns..."
    messages = [
        ModelRequest(parts=[UserPromptPart(content="original task")]),
        ModelResponse(parts=[TextPart(content="a" * 10_000)], model_name="x"),
        ModelRequest(parts=[UserPromptPart(content=synthetic_text)]),
        ModelResponse(parts=[TextPart(content="c" * 10_000)], model_name="x"),
    ]
    result = build_in_run_truncated_history(
        messages=messages,
        model="test:model",
        token_budget=800,
        synthetic_prompt_counts={synthetic_text: 1},
    )
    user_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    # Real user prompt is unconditionally anchored.
    assert "original task" in user_texts


def test_reconcile_synthetic_prompt_counts_drops_missing_entries() -> None:
    from just_another_coding_agent.session.replacement_history import (
        reconcile_synthetic_prompt_counts,
    )

    # Synthetic text not in history anymore: entry dropped.
    counts = {"continuation text": 1, "still here": 2}
    history = [
        ModelRequest(parts=[UserPromptPart(content="still here")]),
        ModelRequest(parts=[UserPromptPart(content="real user prompt")]),
    ]
    reconciled = reconcile_synthetic_prompt_counts(counts, history)
    assert reconciled == {"still here": 1}


def test_reconcile_synthetic_prompt_counts_caps_at_live_occurrences() -> None:
    from just_another_coding_agent.session.replacement_history import (
        reconcile_synthetic_prompt_counts,
    )

    # Tracked 3 but only 2 live in history: cap at 2.
    counts = {"text": 3}
    history = [
        ModelRequest(parts=[UserPromptPart(content="text")]),
        ModelRequest(parts=[UserPromptPart(content="text")]),
    ]
    reconciled = reconcile_synthetic_prompt_counts(counts, history)
    assert reconciled == {"text": 2}


def test_reconcile_synthetic_prompt_counts_empty_input() -> None:
    from just_another_coding_agent.session.replacement_history import (
        reconcile_synthetic_prompt_counts,
    )

    assert reconcile_synthetic_prompt_counts({}, []) == {}
    assert (
        reconcile_synthetic_prompt_counts(
            {"x": 1},
            [ModelRequest(parts=[UserPromptPart(content="y")])],
        )
        == {}
    )


def test_build_in_run_truncated_history_stale_synthetic_count_after_reconcile() -> None:
    # Simulates the post-compaction state: caller has already reconciled
    # synthetic_prompt_counts against the new (compacted) history, so a
    # later real user prompt with the same text as an old (dropped) synthetic
    # is correctly treated as real.
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
        reconcile_synthetic_prompt_counts,
    )

    synthetic_text = "Continue..."
    # User sends a real prompt that happens to equal an old synthetic text.
    # No synthetic occurrence is present in history (was dropped by prior
    # truncation). Reconciling first clears the stale entry.
    history_after_truncation = [
        ModelRequest(parts=[UserPromptPart(content="original task")]),
        ModelRequest(parts=[UserPromptPart(content=synthetic_text)]),
        ModelResponse(parts=[TextPart(content="ok" * 10_000)], model_name="x"),
    ]
    stale_counts = {synthetic_text: 1}
    reconcile_synthetic_prompt_counts(stale_counts, history_after_truncation)
    # One live occurrence, tracked 1 → reconciled == {synthetic_text: 1}.
    # That's still wrong for our scenario, so the test here is to verify the
    # "no live occurrence" path.

    # Stale counts say "1 synthetic", but that's from an older run that's
    # since been compacted away. Live history has 1 occurrence, which is
    # actually a real user prompt the user just sent.
    #
    # Without reconciling, build_in_run_truncated_history consumes the 1
    # match as synthetic and drops it under tight budget.
    # With reconciling, if the live count matches the tracked count we
    # still consume it (we can't distinguish) — but if the PRIOR compaction
    # already dropped the synthetic before the real user sent theirs, the
    # tracked count would have been reconciled to 0 at that time.
    #
    # This test verifies the run-loop-style flow: reconcile against the
    # post-compaction history BEFORE the real prompt is added.
    post_compaction_history = [
        ModelRequest(parts=[UserPromptPart(content="original task")]),
        ModelResponse(parts=[TextPart(content="tail")], model_name="x"),
    ]
    # After compaction, no synthetic occurrences remain:
    reconciled_clean = reconcile_synthetic_prompt_counts(
        {synthetic_text: 1}, post_compaction_history
    )
    assert reconciled_clean == {}

    # Now the real user adds a message with the synthetic text:
    history_with_real = [
        *post_compaction_history,
        ModelRequest(parts=[UserPromptPart(content=synthetic_text)]),
        ModelResponse(parts=[TextPart(content="b" * 10_000)], model_name="x"),
    ]
    # Passing the reconciled (empty) counts, the real prompt is preserved.
    result = build_in_run_truncated_history(
        messages=history_with_real,
        model="test:model",
        token_budget=500,
        synthetic_prompt_counts=reconciled_clean,
    )
    user_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    assert synthetic_text in user_texts
    assert "original task" in user_texts


def test_build_in_run_truncated_history_real_prompt_matching_synthetic_text_stays() -> (
    None
):
    # A real user prompt whose text happens to equal a synthetic correction
    # string must still be treated as a real anchor. Multiset accounting:
    # the first N occurrences (matching the synthetic_prompt_counts) are
    # classified as synthetic, additional occurrences are real.
    from just_another_coding_agent.session.replacement_history import (
        build_in_run_truncated_history,
    )

    shared_text = "Invalid JSON for tool 'read': expected value"
    messages = [
        ModelRequest(parts=[UserPromptPart(content="original task")]),
        # First occurrence of shared_text: marked synthetic via counter
        ModelRequest(parts=[UserPromptPart(content=shared_text)]),
        ModelResponse(parts=[TextPart(content="ok" * 5000)], model_name="x"),
        # Second occurrence: user happened to send this text. Must be real.
        ModelRequest(parts=[UserPromptPart(content=shared_text)]),
        ModelResponse(parts=[TextPart(content="ok2" * 5000)], model_name="x"),
    ]
    result = build_in_run_truncated_history(
        messages=messages,
        model="test:model",
        token_budget=500,
        synthetic_prompt_counts={shared_text: 1},  # only 1 was synthetic
    )
    user_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    # The real user prompt is anchored. The original task too.
    assert "original task" in user_texts
    assert shared_text in user_texts  # the 2nd occurrence, which is real
    # The result should contain exactly 2 UserPromptParts with that text:
    # one would be anchored (the second one), one the original task.
    shared_text_count = sum(1 for t in user_texts if t == shared_text)
    # At least the real (2nd) occurrence is anchored. The synthetic (1st)
    # may or may not be kept depending on budget, but it is NOT an anchor.
    assert shared_text_count >= 1


@pytest.mark.anyio
async def test_in_run_compaction_multi_round_integration_stress(
    tmp_path, monkeypatch
) -> None:
    """Stress-test multiple in-run compactions in a single run.

    Forces compaction to fire ~3 times within one agent.iter by providing a
    fake trigger that returns True every 4th check. Verifies that after
    multiple compactions:

    - All compaction events are emitted as InRunCompactionCompletedEvent
    - The run succeeds
    - The original user prompt survives every compaction
    - Tool-call and tool-return pairing stays balanced across compactions
    - message_history_sink fires exactly once at the terminal
    - The final history is non-empty and coherent
    - Synthetic continuation prompts don't accumulate unboundedly across
      compactions (reconcile is working)
    """
    import json
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import (
        IN_RUN_COMPACTION_CONTINUATION_PROMPT,
        stream_run_events,
    )
    from just_another_coding_agent.tools.deps import WorkspaceDeps

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "# Test project\n\nSome stable project instructions."
    )

    total_tool_rounds = 16  # enough to survive 3 compactions with tail budget
    tool_call_count = 0

    async def stream_fn(
        messages: list[ModelMessage], _info: object
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal tool_call_count
        if tool_call_count < total_tool_rounds:
            tool_call_count += 1
            yield {
                0: DeltaToolCall(
                    name="grow",
                    json_args=json.dumps({"index": tool_call_count}),
                    tool_call_id=f"call-{tool_call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(FunctionModel(stream_function=stream_fn), output_type=str)

    @agent.tool_plain
    async def grow(index: int) -> str:
        return f"result-{index}: " + ("lorem ipsum dolor sit amet " * 200)

    # Deterministic fake trigger: fire compaction every 4 checks, up to 3
    # compactions. This lets us stress the compaction-restart path without
    # depending on exact token math.
    check_count = 0
    compactions_fired = 0
    MAX_FORCED_COMPACTIONS = 3

    def fake_check(
        messages,
        *,
        model,
        last_response_usage=None,
        pending_prompt=None,
    ):
        nonlocal check_count, compactions_fired
        check_count += 1
        if (
            compactions_fired < MAX_FORCED_COMPACTIONS
            and check_count % 4 == 0
            and any(
                isinstance(p, ToolReturnPart)
                for m in messages
                for p in getattr(m, "parts", [])
            )
        ):
            compactions_fired += 1
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    sink_calls: list[list[ModelMessage]] = []

    def recording_sink(messages: Sequence[ModelMessage]) -> None:
        sink_calls.append(list(messages))

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
            prompt="the original user task description",
            deps=WorkspaceDeps.from_workspace_root(workspace),
            message_history_sink=recording_sink,
            available_tool_names=["grow"],
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    from just_another_coding_agent.contracts.run_events import (
        InRunCompactionCompletedEvent,
    )

    # --- Invariant 1: Multiple compactions fired end-to-end ------------------
    compaction_events = [
        e for e in events if isinstance(e, InRunCompactionCompletedEvent)
    ]
    assert len(compaction_events) >= 2, (
        f"Expected at least 2 compactions, got {len(compaction_events)}; "
        f"events: {[e.type for e in events]}"
    )
    # Each compaction_event should have live_message_count > 0 and
    # replacement_message_count > 0.
    for e in compaction_events:
        assert e.live_message_count > 0
        assert e.replacement_message_count > 0

    # --- Invariant 2: Run terminated successfully ----------------------------
    assert events[0].type == "run_started"
    assert events[-1].type == "run_succeeded", f"Final event: {events[-1]}"

    # --- Invariant 3: Sink fired exactly once at terminal --------------------
    assert len(sink_calls) == 1, (
        f"Expected sink to fire once, fired {len(sink_calls)} times"
    )
    authoritative = sink_calls[0]
    assert len(authoritative) > 0

    # --- Invariant 4: Original user prompt survived all compactions ----------
    user_texts = [
        p.content
        for m in authoritative
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    ]
    assert any("the original user task description" in t for t in user_texts), (
        f"Original user prompt lost after compaction; user texts in final "
        f"history: {user_texts}"
    )

    # --- Invariant 5: Tool-call / tool-return pairing is balanced ------------
    call_ids = {
        p.tool_call_id
        for m in authoritative
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolCallPart)
    }
    return_ids = {
        p.tool_call_id
        for m in authoritative
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolReturnPart)
    }
    assert call_ids == return_ids, (
        f"tool call/return pairing broken: calls={call_ids} returns={return_ids}"
    )

    # --- Invariant 6: Continuation prompts don't unboundedly accumulate ------
    # Synthetic continuation prompt may appear 0..N times in the final history
    # where N == number of compactions fired. If the reconcile step is broken,
    # we might see explosive duplication. Assert a soft upper bound.
    continuation_occurrences = sum(
        1
        for m in authoritative
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
        and isinstance(p.content, str)
        and p.content == IN_RUN_COMPACTION_CONTINUATION_PROMPT
    )
    assert continuation_occurrences <= len(compaction_events), (
        f"Continuation prompt duplicated: found {continuation_occurrences} "
        f"in final history after {len(compaction_events)} compactions"
    )

    # --- Invariant 7: Total tool call count reaches termination --------------
    # The mock stream was supposed to run up to total_tool_rounds before
    # saying "done". Verify at least some tool calls fired (not all may
    # survive in the final history due to truncation).
    assert tool_call_count >= total_tool_rounds


@pytest.mark.anyio
async def test_in_run_compaction_sink_includes_compacted_history(
    monkeypatch,
) -> None:
    """Regression: after in-run compaction, message_history_sink must
    receive the COMPACTED history as part of its output, not just the
    latest agent.iter's new_messages().

    This catches the pre-existing bug where the sink formula
    [*carried_messages, *result.new_messages()] only worked when
    carried_messages == current_message_history, which was violated by the
    compaction path that cleared carried while setting current_message_history
    to the replacement.
    """
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from just_another_coding_agent.runtime import run as run_module
    from just_another_coding_agent.runtime.run import stream_run_events

    call_count = 0

    async def tool_loop_stream(
        messages,
        _info,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 4:
            yield {
                0: DeltaToolCall(
                    name="tick",
                    json_args="{}",
                    tool_call_id=f"call-{call_count}",
                )
            }
            return
        yield "done"

    agent = Agent(FunctionModel(stream_function=tool_loop_stream), output_type=str)

    @agent.tool_plain
    async def tick():
        return "ok"

    compaction_triggered = False

    def fake_check(
        messages,
        *,
        model,
        last_response_usage=None,
        pending_prompt=None,
    ):
        nonlocal compaction_triggered
        has_tool_return = any(
            isinstance(p, ToolReturnPart)
            for m in messages
            for p in getattr(m, "parts", [])
        )
        if has_tool_return and not compaction_triggered:
            compaction_triggered = True
            return True
        return False

    monkeypatch.setattr(run_module, "check_in_run_compaction_needed", fake_check)

    sink_calls: list[list[ModelMessage]] = []

    def recording_sink(messages: Sequence[ModelMessage]) -> None:
        sink_calls.append(list(messages))

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
            prompt="UNIQUE_ORIGINAL_TASK_MARKER",
            available_tool_names=["tick"],
            message_history_sink=recording_sink,
            activate_steer_boundary=noop_activate,
            submit_steer_boundary=noop_submit,
            deactivate_steer_boundary=noop_deactivate,
        )
    ]

    assert events[-1].type == "run_succeeded"
    assert compaction_triggered
    assert len(sink_calls) == 1

    # The original user prompt marker must appear somewhere in the sink
    # output even though compaction restarted the agent.iter after it.
    sink_messages = sink_calls[0]
    user_texts = [
        p.content
        for m in sink_messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    ]
    assert "UNIQUE_ORIGINAL_TASK_MARKER" in user_texts, (
        f"Sink lost original prompt after compaction. User texts in sink: {user_texts}"
    )
