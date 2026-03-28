import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from just_another_coding_agent.tui.app import CodingAgentApp
from just_another_coding_agent.tui.rendering import write_stream_event, write_user_turn
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
        prompt_row = app.query_one("#prompt-row", Vertical)
        prompt_input_row = app.query_one("#prompt-input-row", Horizontal)
        prompt_footer = app.query_one("#prompt-footer", Static)

        await asyncio.sleep(
            app.STARTUP_REVEAL_DURATION + (app.STARTUP_REVEAL_STAGGER * 3)
        )
        await _pilot.pause()

        assert prompt_input.has_focus
        assert prompt_input.placeholder == ""
        assert transcript.can_focus is False
        assert transcript.wrap is True
        assert transcript.styles.scrollbar_size_vertical == 0
        assert transcript.styles.scrollbar_size_horizontal == 0
        assert status_bar.styles.opacity == 1
        assert transcript.styles.opacity == 1
        assert prompt_row.styles.opacity == 1
        assert prompt_input_row.styles.opacity == 1
        assert transcript.plain_text.startswith("jaca  ")
        assert "system" not in transcript.plain_text
        assert "idle" in str(status_bar.renderable)
        assert "ollama:test" in str(status_bar.renderable)
        assert transcript.plain_text.count("jaca  ") == 1
        assert transcript.plain_text.count("ollama http://localhost:11434/v1") == 1
        assert "ready" in str(prompt_footer.renderable)
        assert "/help" in str(prompt_footer.renderable)
        assert "recall" in str(prompt_footer.renderable)


@pytest.mark.asyncio
async def test_startup_banner_is_idempotent(tmp_path: Path) -> None:
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
        app._ensure_startup_banner(transcript)
        app._ensure_startup_banner(transcript)

        assert transcript.plain_text.count("jaca  ") == 1
        assert transcript.plain_text.count("ollama http://localhost:11434/v1") == 1


@pytest.mark.asyncio
async def test_startup_banner_flag_prevents_double_rendering(
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
        assert app._startup_banner_rendered is True
        app._ensure_startup_banner(transcript)

        assert transcript.plain_text.count("jaca  ") == 1


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
        output_text = (
            "## Review\n\n"
            "- first point\n"
            "- second point\n\n"
            "1. step one\n"
            "2. step two\n\n"
            "`inline code`"
        )
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
        assert "## Review" in transcript.plain_text
        assert "- first point" in transcript.plain_text
        assert "1. step one" in transcript.plain_text
        assistant_blocks = [
            part.renderable
            for part in transcript._parts
            if isinstance(part.renderable, Text) and "Review" in part.plain_text
        ]
        assert assistant_blocks
        rendered_text = assistant_blocks[-1].plain
        assert "  - " not in rendered_text
        assert "1. " in rendered_text


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
                args={"command": "echo stale preview"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="bash git show HEAD --stat",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="bash",
                tool_call_id="call-1",
                result={"ok": True},
                activity=SimpleNamespace(
                    title="bash git show HEAD --stat",
                    summary="command exited 0",
                    duration_ms=120,
                ),
            ),
        )

        assert "bash  git show HEAD --stat  ok 120ms" in transcript.plain_text
        assert "echo stale preview" not in transcript.plain_text
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
                args={"path": "wrong/path.md", "content": "hello"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="write notes/plan.md",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="write",
                tool_call_id="call-write",
                result="Wrote /tmp/workspace/notes/plan.md",
                activity=SimpleNamespace(
                    title="write notes/plan.md",
                    summary="wrote file",
                    duration_ms=35,
                ),
            ),
        )

        assert "write  notes/plan.md  ok 35ms" in transcript.plain_text
        assert "wrong/path.md" not in transcript.plain_text


@pytest.mark.asyncio
async def test_edit_tool_rows_render_structured_diff_blocks(
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
                tool_name="edit",
                tool_call_id="call-edit",
                args={
                    "path": "src/just_another_coding_agent/tui/widgets.py",
                    "old_text": "old",
                    "new_text": "new",
                },
                args_valid=True,
                activity=SimpleNamespace(
                    title="edit src/just_another_coding_agent/tui/widgets.py",
                    summary=None,
                    duration_ms=None,
                    details=SimpleNamespace(
                        kind="edit",
                        path="src/just_another_coding_agent/tui/widgets.py",
                        diff=None,
                        added_lines=None,
                        removed_lines=None,
                    ),
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="edit",
                tool_call_id="call-edit",
                result=(
                    "Edited /tmp/workspace/src/just_another_coding_agent/tui/"
                    "widgets.py"
                ),
                activity=SimpleNamespace(
                    title="edit src/just_another_coding_agent/tui/widgets.py",
                    summary="edit applied",
                    duration_ms=83,
                    details=SimpleNamespace(
                        kind="edit",
                        path="src/just_another_coding_agent/tui/widgets.py",
                        diff=(
                            "--- src/just_another_coding_agent/tui/widgets.py\n"
                            "+++ src/just_another_coding_agent/tui/widgets.py\n"
                            "@@ -100,2 +100,4 @@\n"
                            " LIVE_FLUSH_DELAY = 0.05\n"
                            " ASSISTANT_LEFT_PAD = 2\n"
                            "-TOOL_OUTPUT_INDENT_STR = \" \"  # aligns with "
                            "marker width\n"
                            "+TOOL_OUTPUT_PREFIX = \"  | \"\n"
                            "+TOOL_OUTPUT_CONT = \"  | \"\n"
                            "+TOOL_OUTPUT_LAST = \"  └ \"\n"
                        ),
                        added_lines=3,
                        removed_lines=1,
                    ),
                ),
            ),
        )

        assert (
            "edit  src/just_another_coding_agent/tui/widgets.py"
            in transcript.plain_text
        )
        assert (
            "Update(src/just_another_coding_agent/tui/widgets.py)"
            in transcript.plain_text
        )
        assert "Added 3 lines, removed 1 line" in transcript.plain_text
        assert "100   LIVE_FLUSH_DELAY = 0.05" in transcript.plain_text
        assert (
            '102 - TOOL_OUTPUT_INDENT_STR = " "  # aligns with marker width'
            in transcript.plain_text
        )
        assert '102 + TOOL_OUTPUT_PREFIX = "  | "' in transcript.plain_text
        assert '104 + TOOL_OUTPUT_LAST = "  └ "' in transcript.plain_text


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
                args={"command": "echo stale preview"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="bash pytest -q",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_failed",
                tool_name="bash",
                tool_call_id="call-1",
                error_type="RuntimeError",
                message="raw runtime message",
                activity=SimpleNamespace(
                    title="bash pytest -q",
                    summary="Command timed out after 60 seconds",
                    duration_ms=1250,
                ),
            ),
        )

        assert (
            "bash  pytest -q  error  Command timed out after 60 seconds  1.2s"
            in transcript.plain_text
        )
        assert "raw runtime message" not in transcript.plain_text


@pytest.mark.asyncio
async def test_resolved_read_misses_are_downgraded_to_muted_not_found_rows(
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
        write_user_turn(transcript, "remove testing section from agents.md")

        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="read",
                tool_call_id="call-read-1",
                args={"path": "agents.md"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="read agents.md",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="read",
                tool_call_id="call-read-1",
                result={
                    "ok": False,
                    "error_type": "ToolPathError",
                    "message": (
                        "[Errno 2] No such file or directory: "
                        f"'{workspace_root / 'agents.md'}'"
                    ),
                },
                activity=SimpleNamespace(
                    title="read agents.md",
                    summary=(
                        "[Errno 2] No such file or directory: "
                        f"'{workspace_root / 'agents.md'}'"
                    ),
                    duration_ms=15,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="assistant_text_delta",
                delta="Let me check the workspace.\n",
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="read",
                tool_call_id="call-read-2",
                args={"path": "AGENTS.md"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="read AGENTS.md",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="read",
                tool_call_id="call-read-2",
                result="# Repository Guidelines\n",
                activity=SimpleNamespace(
                    title="read AGENTS.md",
                    summary="read 1 line",
                    duration_ms=21,
                ),
            ),
        )

        assert "read  AGENTS.md  ok 21ms" in transcript.plain_text
        assert "read  agents.md  not found  15ms" in transcript.plain_text
        assert "read  agents.md  error" not in transcript.plain_text
        assert "No such file or directory" not in transcript.plain_text


@pytest.mark.asyncio
async def test_resolved_read_misses_downgrade_across_path_variants(
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
        write_user_turn(transcript, "find agents instructions")

        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="read",
                tool_call_id="call-read-1",
                args={"path": "agents.md"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="read agents.md",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="read",
                tool_call_id="call-read-1",
                result={
                    "ok": False,
                    "error_type": "ToolPathError",
                    "message": "No such file or directory",
                },
                activity=SimpleNamespace(
                    title="read agents.md",
                    summary="No such file or directory",
                    duration_ms=11,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="read",
                tool_call_id="call-read-2",
                args={"path": "./docs/AGENTS.md"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="read ./docs/AGENTS.md",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="read",
                tool_call_id="call-read-2",
                result="found it",
                activity=SimpleNamespace(
                    title="read ./docs/AGENTS.md",
                    summary="read 1 line",
                    duration_ms=18,
                ),
            ),
        )

        assert "read  agents.md  not found  11ms" in transcript.plain_text
        assert "read  agents.md  error" not in transcript.plain_text


@pytest.mark.asyncio
async def test_tool_error_results_prefer_backend_activity_metadata(
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
                tool_name="read",
                tool_call_id="call-read",
                args={"path": "stale.txt"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="read missing.txt",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="read",
                tool_call_id="call-read",
                result={
                    "ok": False,
                    "error_type": "ToolPathError",
                    "message": "stale low-level message",
                },
                activity=SimpleNamespace(
                    title="read missing.txt",
                    summary="No such file or directory",
                    duration_ms=8,
                ),
            ),
        )

        assert (
            "read  missing.txt  error  No such file or directory  8ms"
            in transcript.plain_text
        )
        assert "stale.txt" not in transcript.plain_text
        assert "stale low-level message" not in transcript.plain_text


@pytest.mark.asyncio
async def test_tool_rows_preserve_interleaved_assistant_sequence(
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
                type="assistant_text_delta",
                delta="Checking the repo...\n",
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="bash",
                tool_call_id="call-1",
                args={"command": "git status --short"},
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
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="assistant_text_delta",
                delta="Working tree is clean.",
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="run_succeeded",
                output_text="Working tree is clean.",
            ),
        )

        first = transcript.plain_text.index("Checking the repo...")
        tool = transcript.plain_text.index("bash  git status --short")
        second = transcript.plain_text.index("Working tree is clean.")

        assert first < tool < second


@pytest.mark.asyncio
async def test_consecutive_tool_calls_share_one_live_group_block(
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
        transcript.clear()

        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="bash",
                tool_call_id="call-1",
                args={"command": "git log --oneline -5"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="bash git log --oneline -5",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="bash",
                tool_call_id="call-1",
                result={"ok": True},
                activity=SimpleNamespace(
                    title="bash git log --oneline -5",
                    summary="command exited 0",
                    duration_ms=19,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_started",
                tool_name="bash",
                tool_call_id="call-2",
                args={"command": "git show --stat HEAD"},
                args_valid=True,
                activity=SimpleNamespace(
                    title="bash git show --stat HEAD",
                    summary=None,
                    duration_ms=None,
                ),
            ),
        )
        write_stream_event(
            transcript,
            SimpleNamespace(
                type="tool_call_succeeded",
                tool_name="bash",
                tool_call_id="call-2",
                result={"ok": True},
                activity=SimpleNamespace(
                    title="bash git show --stat HEAD",
                    summary="command exited 0",
                    duration_ms=28,
                ),
            ),
        )

        assert len(transcript._parts) == 1
        assert "bash  git log --oneline -5  ok 19ms" in transcript.plain_text
        assert "bash  git show --stat HEAD  ok 28ms" in transcript.plain_text

        write_stream_event(
            transcript,
            SimpleNamespace(
                type="assistant_text_delta",
                delta="Done reviewing commits.",
            ),
        )

        assert len(transcript._parts) == 2
        assert transcript.plain_text.endswith("Done reviewing commits.")


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
