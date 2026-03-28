import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.markdown import Markdown
from textual.containers import Horizontal
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
        prompt_row = app.query_one("#prompt-row", Horizontal)

        await asyncio.sleep(
            app.STARTUP_REVEAL_DURATION + (app.STARTUP_REVEAL_STAGGER * 3)
        )
        await _pilot.pause()

        assert prompt_input.has_focus
        assert transcript.can_focus is False
        assert transcript.wrap is True
        assert transcript.styles.scrollbar_size_vertical == 0
        assert transcript.styles.scrollbar_size_horizontal == 0
        assert status_bar.styles.opacity == 1
        assert transcript.styles.opacity == 1
        assert prompt_row.styles.opacity == 1
        assert transcript.plain_text.startswith("jaca  ")
        assert "system" not in transcript.plain_text
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
            SimpleNamespace(type="run_succeeded", output_text="Hello world"),
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
            SimpleNamespace(type="run_succeeded", output_text="Hello world"),
        )
        self._finish_stream_feedback(succeeded=True)


class DemoInterruptedApp(CodingAgentApp):
    async def _run_prompt(self, prompt: str) -> None:
        transcript = self.query_one("#output", TranscriptLog)
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta="Working"),
        )
        self._interrupt_requested = True
        transcript.write_line("stream interrupted")
        self._finish_stream_feedback(succeeded=False)


class DemoMarkdownApp(CodingAgentApp):
    async def _run_prompt(self, prompt: str) -> None:
        transcript = self.query_one("#output", TranscriptLog)
        output_text = "## Review\n\n- first point\n- second point\n\n`inline code`"
        write_stream_event(
            transcript,
            SimpleNamespace(type="assistant_text_delta", delta=output_text),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(type="run_succeeded", output_text=output_text),
        )
        self._finish_stream_feedback(succeeded=True)


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
        assert "Hello world" in transcript.plain_text
        assert "assistant" not in transcript.plain_text


@pytest.mark.asyncio
async def test_completed_assistant_turn_is_rendered_as_markdown(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = DemoMarkdownApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )

    async with app.run_test() as pilot:
        await pilot.press("r", "e", "v", "i", "e", "w", "enter")
        transcript = app.query_one("#output", TranscriptLog)
        markdown_parts = [
            part.renderable
            for part in transcript._parts
            if isinstance(part.renderable, Markdown)
        ]

        assert markdown_parts
        assert "## Review" in transcript.plain_text
        assert "- first point" in transcript.plain_text


@pytest.mark.asyncio
async def test_slash_help_renders_as_system_block(tmp_path: Path) -> None:
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
        await pilot.press("slash", "h", "e", "l", "p", "enter")
        transcript = app.query_one("#output", TranscriptLog)
        assert "note  commands" in transcript.plain_text
        assert "keyboard" in transcript.plain_text


@pytest.mark.asyncio
async def test_tool_activity_rows_show_preview_and_success_state(
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

    async with app.run_test() as _pilot:
        transcript = app.query_one("#output", TranscriptLog)
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="bash",
                tool_call_id="call-1",
                args={"command": "git show HEAD --stat"},
                args_valid=True,
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="bash",
                tool_call_id="call-1",
                result={"ok": True},
            ),
        )

        assert "bash ok  git show HEAD --stat" in transcript.plain_text
        assert "bash x2" not in transcript.plain_text
        assert "tool bash" not in transcript.plain_text


@pytest.mark.asyncio
async def test_file_tool_rows_use_path_preview_and_success_state(
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

    async with app.run_test() as _pilot:
        transcript = app.query_one("#output", TranscriptLog)
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="write",
                tool_call_id="call-write",
                args={"path": "notes/plan.md", "content": "hello"},
                args_valid=True,
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="write",
                tool_call_id="call-write",
                result="Wrote /tmp/workspace/notes/plan.md",
            ),
        )

        assert "write ok  notes/plan.md" in transcript.plain_text


@pytest.mark.asyncio
async def test_tool_failures_render_as_preview_aware_error_rows(
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

    async with app.run_test() as _pilot:
        transcript = app.query_one("#output", TranscriptLog)
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="bash",
                tool_call_id="call-1",
                args={"command": "pytest -q"},
                args_valid=True,
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_failed",
                tool_name="bash",
                tool_call_id="call-1",
                error_type="RuntimeError",
                message="Command timed out after 60 seconds",
            ),
        )

        assert (
            "bash error  pytest -q  |  Command timed out after 60 seconds"
            in transcript.plain_text
        )


@pytest.mark.asyncio
async def test_prompt_history_recall_and_draft_restore(tmp_path: Path) -> None:
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
        prompt_input = app.query_one("#prompt-input", Input)

        await pilot.press("f", "i", "r", "s", "t", "enter")
        await pilot.pause()
        await pilot.press("s", "e", "c", "o", "n", "d", "enter")
        await pilot.pause()

        await pilot.press("d", "r", "a", "f", "t")
        assert prompt_input.value == "draft"

        await pilot.press("up")
        assert prompt_input.value == "second"

        await pilot.press("up")
        assert prompt_input.value == "first"

        await pilot.press("down")
        assert prompt_input.value == "second"

        await pilot.press("down")
        assert prompt_input.value == "draft"


@pytest.mark.asyncio
async def test_ctrl_u_clears_prompt_input(tmp_path: Path) -> None:
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
        prompt_input = app.query_one("#prompt-input", Input)
        await pilot.press("h", "e", "l", "l", "o")
        assert prompt_input.value == "hello"

        await pilot.press("ctrl+u")
        assert prompt_input.value == ""


@pytest.mark.asyncio
async def test_success_phase_settles_before_returning_to_idle(tmp_path: Path) -> None:
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
    app.COMPLETION_SETTLE_DELAY = 0.3

    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        status_bar = app.query_one("#status-bar", StatusBar)
        prompt_marker = app.query_one("#prompt-marker", Static)

        assert "completed" in str(status_bar.renderable)
        assert status_bar.has_class("phase-completed")
        assert str(prompt_marker.renderable) == "ok"

        await asyncio.sleep(app.COMPLETION_SETTLE_DELAY + 0.1)
        await pilot.pause()
        assert "idle" in str(status_bar.renderable)
        assert status_bar.has_class("phase-idle")


@pytest.mark.asyncio
async def test_interrupt_phase_settles_before_returning_to_idle(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    app = DemoInterruptedApp(
        model="ollama:test",
        workspace_root=workspace_root,
        sessions_root=sessions_root,
        thinking=None,
    )
    app.INTERRUPT_SETTLE_DELAY = 0.3

    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        status_bar = app.query_one("#status-bar", StatusBar)
        prompt_marker = app.query_one("#prompt-marker", Static)

        assert "interrupted" in str(status_bar.renderable)
        assert status_bar.has_class("phase-interrupted")
        assert str(prompt_marker.renderable) == "!!"

        await asyncio.sleep(app.INTERRUPT_SETTLE_DELAY + 0.1)
        await pilot.pause()
        assert "idle" in str(status_bar.renderable)
        assert status_bar.has_class("phase-idle")


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
