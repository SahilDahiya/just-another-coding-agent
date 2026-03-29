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
)


def test_compaction_public_api_is_split_across_submodules() -> None:
    assert (
        in_run.build_in_run_history_processor
        is build_in_run_history_processor
    )
    assert (
        in_run.restore_in_run_compaction_from_messages
        is restore_in_run_compaction_from_messages
    )

    assert resume.build_resume_message_history is build_resume_message_history
    assert (
        resume.build_compaction_summary_message
        is build_compaction_summary_message
    )
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
    assert (
        session_summary.should_auto_compact_session
        is should_auto_compact_session
    )
