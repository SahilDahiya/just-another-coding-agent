from types import SimpleNamespace

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.contracts.session import SessionCompactionSummary
from just_another_coding_agent.runtime.compaction import (
    build_compaction_summary_instructions,
    build_resume_instructions,
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
)


def test_compaction_public_api_is_split_across_submodules() -> None:
    assert resume.build_resume_message_history is build_resume_message_history
    assert (
        resume.build_compaction_summary_instructions
        is build_compaction_summary_instructions
    )
    assert resume.build_resume_instructions is build_resume_instructions

    assert (
        session_summary.summarize_session_for_compaction
        is summarize_session_for_compaction
    )
    assert (
        session_summary.summarize_and_append_compaction_to_session
        is summarize_and_append_compaction_to_session
    )
    assert session_summary.should_auto_compact_session is should_auto_compact_session


def test_estimate_resume_history_tokens_counts_resume_instructions_with_measured_usage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        trigger,
        "build_resume_message_history",
        lambda _loaded_session: [
            ModelResponse(parts=[TextPart(content="done")], model_name="test"),
        ],
    )
    monkeypatch.setattr(
        trigger,
        "build_resume_instructions",
        lambda _loaded_session: "x" * 40,
    )
    monkeypatch.setattr(
        trigger,
        "_find_last_measured_usage_tokens",
        lambda _messages: (120, 0),
    )

    estimate = trigger._estimate_resume_history_budget_components(
        SimpleNamespace(latest_compaction=None)
    )

    assert estimate.estimated_resume_message_tokens == 120
    assert estimate.estimated_summary_tokens == 10
    assert estimate.estimated_resume_history_tokens == 130
    assert estimate.measured_usage_tokens == 120
    assert estimate.estimated_trailing_tokens == 0


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
    monkeypatch.setattr(
        trigger,
        "_estimate_resume_history_budget_components",
        lambda loaded_session: trigger._ResumeHistoryBudgetEstimate(
            estimated_resume_message_tokens=43_000,
            estimated_resume_history_tokens=43_000,
            estimated_checkpoint_tokens=0,
            estimated_summary_tokens=0,
        ),
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
    monkeypatch.setattr(
        trigger,
        "_estimate_resume_history_budget_components",
        lambda loaded_session: trigger._ResumeHistoryBudgetEstimate(
            estimated_resume_message_tokens=42_700,
            estimated_resume_history_tokens=43_000,
            estimated_checkpoint_tokens=900,
            estimated_summary_tokens=300,
            measured_usage_tokens=120,
            estimated_trailing_tokens=42_880,
        ),
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
    assert report.estimated_resume_message_tokens == 42_700
    assert report.estimated_resume_history_tokens == 43_000
    assert report.estimated_checkpoint_tokens == 900
    assert report.estimated_summary_tokens == 300
    assert report.measured_usage_tokens == 120
    assert report.estimated_trailing_tokens == 42_880
    assert report.estimated_post_compaction_headroom_tokens == 25_000
    assert report.runs_since_latest_compaction == 1


def test_build_resume_instructions_uses_latest_compaction_summary(tmp_path) -> None:
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
        summary=SessionCompactionSummary(
            current_objective="ship the fix",
            current_plan=["run tests"],
            unresolved_work=["send the final update"],
        ),
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    instructions = build_resume_instructions(loaded)

    assert instructions is not None
    assert instructions.startswith(
        "Continue from this internal session continuity state."
    )
    assert "Current objective: ship the fix" in instructions
    assert "Current plan:" in instructions
    assert "- run tests" in instructions
    assert "Unresolved work:" in instructions
    assert "- send the final update" in instructions


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
        summary=SessionCompactionSummary(
            current_objective="continue from retained runs",
            established_facts=["run-1 is summarized"],
            user_preferences=[],
            important_paths=[],
            read_paths=[],
            modified_paths=[],
            recent_shell_commands=[],
            recent_failures=[],
            open_questions=[],
            unresolved_work=["finish the task"],
        ),
        summarized_through_run_id="run-1",
        first_kept_run_id="run-2",
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
