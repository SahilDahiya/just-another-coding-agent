from pathlib import Path

from just_another_coding_agent.tui.rendering import (
    build_phase_label,
    build_prompt_marker_text,
    build_status_text,
)
from just_another_coding_agent.tui.state import UiPhase, UiState


def test_build_status_text_includes_phase_and_session() -> None:
    state = UiState(
        model="ollama:test",
        workspace_root=Path("/tmp/workspace"),
        thinking="medium",
        session_id="1234567890abcdef",
        phase=UiPhase.STREAMING,
    )

    status = build_status_text(state, motion_tick=1)

    assert "streaming.." in status
    assert "ollama:test" in status
    assert "/tmp/workspace" in status
    assert "thinking" in status
    assert "session" in status


def test_motion_helpers_are_calm_when_idle_and_active_when_busy() -> None:
    assert build_phase_label(UiPhase.IDLE, 2) == "idle"
    assert build_prompt_marker_text(UiPhase.IDLE, 2) == "> "

    assert build_phase_label(UiPhase.STREAMING, 0) == "streaming."
    assert build_phase_label(UiPhase.STREAMING, 2) == "streaming..."
    assert build_prompt_marker_text(UiPhase.STREAMING, 0) == ">>"
    assert build_prompt_marker_text(UiPhase.STREAMING, 1) == "> "

    assert build_phase_label(UiPhase.COMPACTING, 1) == "compacting.."
    assert build_prompt_marker_text(UiPhase.COMPACTING, 0) == "::"
    assert build_prompt_marker_text(UiPhase.INTERRUPTED, 0) == "!!"
    assert build_prompt_marker_text(UiPhase.ERROR, 0) == "x "


def test_ui_state_helpers_return_updated_copies(tmp_path: Path) -> None:
    state = UiState(
        model="ollama:test",
        workspace_root=tmp_path,
        thinking=None,
    )

    updated = (
        state.with_model("openai:test")
        .with_thinking("high")
        .with_session_id("abc123")
        .with_phase(UiPhase.ERROR)
    )

    assert state.model == "ollama:test"
    assert state.phase == UiPhase.IDLE
    assert updated.model == "openai:test"
    assert updated.thinking == "high"
    assert updated.session_id == "abc123"
    assert updated.phase == UiPhase.ERROR
