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
from just_another_coding_agent.runtime.compaction import (
    build_compaction_summary_message,
    build_in_run_history_processor,
    build_resume_message_history,
    in_run,
    restore_in_run_compaction_from_messages,
    resume,
    session_summary,
    should_auto_compact_session,
    strip_compaction_summary_from_messages,
    summarize_and_append_compaction_to_session,
    summarize_session_for_compaction,
    trigger,
)
from just_another_coding_agent.runtime.compaction.budget import (
    build_effective_compaction_context_window_tokens,
)
from just_another_coding_agent.session import append_run_to_session, load_session


def test_compaction_public_api_is_split_across_submodules() -> None:
    assert in_run.build_in_run_history_processor is build_in_run_history_processor
    assert (
        in_run.restore_in_run_compaction_from_messages
        is restore_in_run_compaction_from_messages
    )

    assert resume.build_resume_message_history is build_resume_message_history
    assert resume.build_compaction_summary_message is build_compaction_summary_message
    assert (
        resume.strip_compaction_summary_from_messages
        is strip_compaction_summary_from_messages
    )

    assert (
        session_summary.summarize_session_for_compaction
        is summarize_session_for_compaction
    )
    assert (
        session_summary.summarize_and_append_compaction_to_session
        is summarize_and_append_compaction_to_session
    )
    assert session_summary.should_auto_compact_session is should_auto_compact_session


def test_estimate_resume_history_tokens_prefers_last_usage_plus_trailing_growth(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="first",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="first")]),
            ModelResponse(
                parts=[TextPart(content="done")],
                usage=RequestUsage(input_tokens=120, output_tokens=8),
                model_name="test",
            ),
        ],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )
    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="second",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="second " * 20)]),
            ModelResponse(parts=[TextPart(content="later")], model_name="test"),
        ],
        events=[
            RunStartedEvent(run_id="run-2"),
            RunSucceededEvent(run_id="run-2", output_text="later"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    resume_history = trigger.build_resume_message_history(loaded)
    trailing_messages = resume_history[2:]

    assert trigger._estimate_resume_history_tokens(loaded) == 120 + (
        -(-trigger._estimate_message_history_chars(trailing_messages) // 4)
    )


def test_estimate_resume_history_tokens_falls_back_to_whole_history_heuristic(
    tmp_path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_run_to_session(
        path=session_path,
        workspace_root=workspace_root,
        prompt="only",
        thinking=None,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="only " * 20)]),
            ModelResponse(parts=[TextPart(content="done")], model_name="test"),
        ],
        events=[
            RunStartedEvent(run_id="run-1"),
            RunSucceededEvent(run_id="run-1", output_text="done"),
        ],
    )

    loaded = load_session(path=session_path, workspace_root=workspace_root)
    resume_history = trigger.build_resume_message_history(loaded)

    assert trigger._estimate_resume_history_tokens(loaded) == (
        -(-trigger._estimate_message_history_chars(resume_history) // 4)
    )


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
        "_estimate_resume_history_tokens",
        lambda loaded_session: 43_000,
    )

    assert trigger.should_auto_compact_session(
        loaded,
        model="test:model",
        get_context_window_tokens=lambda _model: 100_000,
    )
