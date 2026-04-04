from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

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


def test_estimate_resume_history_budget_components_use_replacement_history(
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(
        trigger,
        "_estimate_resume_history_budget_components",
        lambda loaded_session, *, model: trigger._ResumeHistoryBudgetEstimate(
            estimation_method="chars_per_token_v1",
            estimated_resume_message_tokens=43_000,
            estimated_replacement_messages_tokens=0,
            estimated_replacement_summary_tokens=0,
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
        lambda loaded_session, *, model: trigger._ResumeHistoryBudgetEstimate(
            estimation_method="chars_per_token_v1",
            estimated_resume_message_tokens=42_700,
            estimated_replacement_messages_tokens=900,
            estimated_replacement_summary_tokens=300,
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
    assert report.estimation_method == "chars_per_token_v1"
    assert report.estimated_resume_message_tokens == 42_700
    assert report.estimated_replacement_messages_tokens == 900
    assert report.estimated_replacement_summary_tokens == 300
    assert report.estimated_post_compaction_headroom_tokens == 25_300
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
