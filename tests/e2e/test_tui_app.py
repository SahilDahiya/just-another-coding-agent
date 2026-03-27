import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input, Static

from just_another_coding_agent.tui.app import CodingAgentApp
from just_another_coding_agent.tui.rendering import write_stream_event
from just_another_coding_agent.tui.state import UiPhase
from just_another_coding_agent.tui.widgets import StatusBar, TranscriptLog


@pytest.mark.asyncio
async def test_tui_app_starts_and_focuses_prompt(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking="medium",
    )

    async with app.run_test() as _pilot:
        prompt_input = app.query_one("#prompt-input", Input)
        transcript = app.query_one("#output", TranscriptLog)
        status_bar = app.query_one("#status-bar", StatusBar)

        assert prompt_input.has_focus
        assert transcript.can_focus is False
        assert transcript.wrap is True
        assert transcript.styles.scrollbar_size_vertical == 0
        assert transcript.styles.scrollbar_size_horizontal == 0
        assert "idle" in str(status_bar.renderable)
        assert "ollama:test" in str(status_bar.renderable)


@pytest.mark.asyncio
async def test_streamed_assistant_deltas_append_as_text(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as _pilot:
        transcript = app.query_one("#output", TranscriptLog)
        transcript.write("\n")

        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta="Hello"),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta=" world"),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="run_succeeded"),
        )

        assert "Hello world" in transcript.plain_text


@pytest.mark.asyncio
async def test_live_transcript_batches_rerender_per_flush_window(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as pilot:
        transcript = app.query_one("#output", TranscriptLog)
        rerender_count = 0
        original_rerender = transcript._rerender

        def counted_rerender() -> None:
            nonlocal rerender_count
            rerender_count += 1
            original_rerender()

        transcript._rerender = counted_rerender  # type: ignore[method-assign]

        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta="Hello"),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta=" world"),
        )
        assert rerender_count == 0

        await asyncio.sleep(transcript.LIVE_FLUSH_DELAY * 2)
        await pilot.pause()
        assert rerender_count == 1
        assert "Hello world" in transcript.plain_text


@pytest.mark.asyncio
async def test_transcript_wraps_without_losing_scroll_behavior(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test(size=(60, 12)) as pilot:
        transcript = app.query_one("#output", TranscriptLog)
        assert transcript.wrap is True
        assert transcript.styles.scrollbar_size_vertical == 0
        assert transcript.styles.scrollbar_size_horizontal == 0

        for index in range(40):
            transcript.write_line(
                f"line {index} this transcript should wrap long content instead of "
                "using in-app scrollbar chrome"
            )

        await pilot.pause()
        assert transcript.max_scroll_y > 0

        transcript.scroll_home(animate=False)
        await pilot.pause()
        assert transcript.scroll_y == 0

        transcript.scroll_end(animate=False)
        await pilot.pause()
        assert transcript.scroll_y == transcript.max_scroll_y


class DemoStreamingApp(CodingAgentApp):
    async def _run_prompt(self, prompt: str) -> None:
        transcript = self.query_one("#output", TranscriptLog)
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta="Hello"),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta=" world"),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="run_succeeded"),
        )


@pytest.mark.asyncio
async def test_prompt_submission_keeps_spaces_and_streams_single_line(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = DemoStreamingApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o", "space", "w", "o", "r", "l", "d")
        prompt_input = app.query_one("#prompt-input", Input)
        assert prompt_input.value == "hello world"

        await pilot.press("enter")

        transcript = app.query_one("#output", TranscriptLog)
        assert "> hello world" in transcript.plain_text
        assert "assistant" in transcript.plain_text
        assert "Hello world" in transcript.plain_text


@pytest.mark.asyncio
async def test_status_bar_updates_for_explicit_ui_states(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as _pilot:
        status_bar = app.query_one("#status-bar", StatusBar)
        assert "idle" in str(status_bar.renderable)

        app._set_phase(UiPhase.STREAMING)
        assert "streaming." in str(status_bar.renderable)
        assert status_bar.has_class("phase-streaming")

        app._set_phase(UiPhase.ERROR)
        assert "error" in str(status_bar.renderable)
        assert status_bar.has_class("phase-error")


@pytest.mark.asyncio
async def test_prompt_marker_pulses_for_active_states(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as _pilot:
        prompt_marker = app.query_one("#prompt-marker", Static)

        app._set_phase(UiPhase.STREAMING)
        app._motion_tick = 0
        app._refresh_shell_chrome()
        assert str(prompt_marker.renderable) == ">>"

        app._motion_tick = 1
        app._refresh_shell_chrome()
        assert str(prompt_marker.renderable) == "> "

        app._set_phase(UiPhase.ERROR)
        assert str(prompt_marker.renderable) == "x "
