from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input

from just_another_coding_agent.tui.app import CodingAgentApp
from just_another_coding_agent.tui.rendering import write_stream_event
from just_another_coding_agent.tui.widgets import OutputScroll, StatusBar, TranscriptLog


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
        transcript_scroll = app.query_one("#output-scroll", OutputScroll)
        status_bar = app.query_one("#status-bar", StatusBar)

        assert prompt_input.has_focus
        assert transcript.can_focus is False
        assert transcript_scroll.can_focus is False
        assert "model" in str(status_bar.renderable)
        assert "workspace" in str(status_bar.renderable)


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

        assert transcript.lines[-2:] == ["Hello world", ""]


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
        assert "> hello world" in transcript.lines
        assert "Hello world" in transcript.lines
