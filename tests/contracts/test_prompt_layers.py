from datetime import date

from just_another_coding_agent.runtime.project_docs import (
    build_project_doc_prefix_messages,
)
from just_another_coding_agent.runtime.prompt_layers import (
    build_base_product_prompt,
    build_prompt_context_layers,
)
from just_another_coding_agent.runtime.turn_context import (
    build_runtime_context_injection_plan,
    build_session_turn_context_entry,
    evaluate_turn_context_baseline,
)


def _message_texts(messages) -> list[str]:
    return [message.parts[0].content for message in messages]


def test_prompt_context_layers_match_existing_context_helpers(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    model = "openai-responses:gpt-5.3-codex"

    layers = build_prompt_context_layers(
        baseline_decision=None,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 10),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
    )

    _, expected_project_messages = build_project_doc_prefix_messages(workspace_root)
    expected_runtime_plan = build_runtime_context_injection_plan(
        baseline_decision=None,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 10),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
    )

    assert layers.base_instructions == build_base_product_prompt()
    assert _message_texts(layers.project_messages) == _message_texts(
        expected_project_messages
    )
    assert (
        _message_texts(layers.runtime_before_history_messages)
        == _message_texts(expected_runtime_plan.before_history_messages)
    )
    assert (
        _message_texts(layers.runtime_after_history_messages)
        == _message_texts(expected_runtime_plan.after_history_messages)
    )
    assert layers.mode_messages == ()
    assert _message_texts(layers.before_history_messages) == _message_texts(
        (
            *expected_project_messages,
            *expected_runtime_plan.before_history_messages,
        )
    )
    assert _message_texts(layers.after_history_messages) == _message_texts(
        expected_runtime_plan.after_history_messages
    )


def test_prompt_context_layers_keep_runtime_diff_after_history(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    model = "openai-responses:gpt-5.3-codex"
    entry = build_session_turn_context_entry(
        run_id="run-1",
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 9),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
    )
    decision = evaluate_turn_context_baseline(
        entry=entry,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 10),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
        has_persisted_history=True,
    )

    layers = build_prompt_context_layers(
        baseline_decision=decision,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 10),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
    )
    expected_runtime_plan = build_runtime_context_injection_plan(
        baseline_decision=decision,
        model=model,
        workspace_root=workspace_root,
        current_date=date(2026, 4, 10),
        shell_family="posix",
        timezone="America/Los_Angeles",
        thinking="medium",
    )

    assert (
        _message_texts(layers.before_history_messages)
        == _message_texts(expected_runtime_plan.before_history_messages)
    )
    assert _message_texts(layers.after_history_messages) == _message_texts(
        expected_runtime_plan.after_history_messages
    )
