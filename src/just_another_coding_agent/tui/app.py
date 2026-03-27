"""Main Textual application for the coding agent TUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.driver import Driver
from textual.drivers.linux_driver import LinuxDriver
from textual.drivers.linux_inline_driver import LinuxInlineDriver
from textual.widgets import Input, Static

from .commands import handle_provider_command, write_help
from .drivers import (
    VscodeLinuxDriver,
    VscodeLinuxInlineDriver,
    running_in_vscode_terminal,
)
from .rendering import (
    build_prompt_marker_text,
    display_path,
    resolve_thinking_setting,
    update_status_bar,
    write_startup_banner,
    write_stream_event,
)
from .state import UiPhase, UiState
from .widgets import APP_CSS, StatusBar, TranscriptLog


class CodingAgentApp(App[None]):
    """Interactive TUI for the coding agent."""

    TITLE = "jaca"

    CSS = APP_CSS

    PHASE_CLASSES = tuple(f"phase-{phase}" for phase in UiPhase)

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt/Quit", priority=True),
    ]

    def __init__(
        self,
        *,
        model: Any,
        workspace_root: Path,
        sessions_root: Path,
        thinking: str | None = None,
    ) -> None:
        super().__init__()
        self._sessions_root = sessions_root
        self._state = UiState(
            model=model,
            workspace_root=workspace_root,
            thinking=thinking,
        )
        self._streaming = False
        self._interrupt_requested = False
        self._last_interrupt_time: float = 0.0
        self._motion_tick = 0

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Vertical(id="main"):
            yield TranscriptLog(id="output")
            with Horizontal(id="prompt-row"):
                yield Static("> ", id="prompt-marker")
                yield Input(
                    placeholder="/help for commands",
                    id="prompt-input",
                )

    def get_driver_class(self) -> type[Driver]:
        """Select a Textual driver, with a VS Code terminal workaround."""
        driver_class = super().get_driver_class()
        if not running_in_vscode_terminal():
            return driver_class
        if driver_class is LinuxDriver:
            return VscodeLinuxDriver
        if driver_class is LinuxInlineDriver:
            return VscodeLinuxInlineDriver
        return driver_class

    def on_mount(self) -> None:
        self.set_interval(0.24, self._advance_motion)
        self._refresh_shell_chrome()
        self.query_one("#prompt-input", Input).focus()
        output = self.query_one("#output", TranscriptLog)
        write_startup_banner(
            output,
            model=self._state.model,
            workspace_root=self._state.workspace_root,
            thinking=self._state.thinking,
        )

    def _update_status_bar(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        update_status_bar(status, state=self._state, motion_tick=self._motion_tick)

    def _refresh_shell_chrome(self) -> None:
        self._update_status_bar()
        prompt_marker = self.query_one("#prompt-marker", Static)
        prompt_marker.update(
            build_prompt_marker_text(self._state.phase, self._motion_tick)
        )
        phase_class = f"phase-{self._state.phase}"
        for widget in (
            self.query_one("#status-bar", StatusBar),
            self.query_one("#prompt-row", Horizontal),
            prompt_marker,
        ):
            widget.remove_class(*self.PHASE_CLASSES)
            widget.add_class(phase_class)

    def _advance_motion(self) -> None:
        self._motion_tick += 1
        if self.is_mounted:
            self._refresh_shell_chrome()

    def _set_phase(self, phase: UiPhase) -> None:
        self._state = self._state.with_phase(phase)
        if self.is_mounted:
            self._refresh_shell_chrome()

    def action_interrupt(self) -> None:
        import time

        now = time.monotonic()
        if self._streaming:
            self._interrupt_requested = True
            self._set_phase(UiPhase.INTERRUPTED)
            output = self.query_one("#output", TranscriptLog)
            output.write("\n")
            output.write_line("interrupted")
            self._last_interrupt_time = now
            return

        if now - self._last_interrupt_time < 2.0:
            self.exit()
            return

        self._last_interrupt_time = now
        self.notify("ctrl+c again to quit", severity="warning", timeout=2)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._streaming:
            return

        event.input.clear()

        if prompt.startswith("/"):
            await self._handle_slash_command(prompt)
            return

        output = self.query_one("#output", TranscriptLog)
        output.write("\n")
        output.write_line(f"> {prompt}")
        output.write("\n")
        output.write_line("assistant")
        output.write("\n")

        self._streaming = True
        self._interrupt_requested = False
        self._set_phase(UiPhase.STREAMING)
        try:
            await self._run_prompt(prompt)
        except Exception as error:
            self._set_phase(UiPhase.ERROR)
            error_msg = str(error)
            if "api_key" in error_msg.lower():
                output.write_line(f"ERROR: {error_msg}")
                output.write_line("use /login <key> to set your API key")
            else:
                output.write_line(f"ERROR: {error_msg}")
        finally:
            self._streaming = False
            self._interrupt_requested = False
            if self._state.phase != UiPhase.ERROR:
                self._set_phase(UiPhase.IDLE)

    async def _handle_slash_command(self, command: str) -> None:
        output = self.query_one("#output", TranscriptLog)
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "/help":
            write_help(output)

        elif cmd == "/provider":
            handle_provider_command(arg, output)

        elif cmd == "/model":
            if arg:
                self._state = self._state.with_model(arg)
                output.write_line(f"model set to {self._state.model}")
            else:
                output.write_line(f"model: {self._state.model}")
            self._refresh_shell_chrome()

        elif cmd == "/thinking":
            if arg:
                valid = {"true", "false", "minimal", "low", "medium", "high", "xhigh"}
                if arg.lower() in valid:
                    self._state = self._state.with_thinking(arg.lower())
                    output.write_line(f"thinking set to {self._state.thinking}")
                else:
                    output.write_line(
                        f"ERROR: invalid. use: {', '.join(sorted(valid))}"
                    )
            else:
                output.write_line(f"thinking: {self._state.thinking or 'default'}")
            self._refresh_shell_chrome()

        elif cmd == "/workspace":
            output.write_line(f"workspace: {display_path(self._state.workspace_root)}")

        elif cmd == "/session":
            if self._state.session_id:
                output.write_line(f"session: {self._state.session_id}")
            else:
                output.write_line("no active session")

        elif cmd == "/compact":
            if self._state.session_id is None:
                output.write_line("ERROR: no active session")
                return
            self._set_phase(UiPhase.COMPACTING)
            output.write_line("compacting...")
            try:
                await self._compact_session()
                self._set_phase(UiPhase.IDLE)
                output.write_line("session compacted")
            except Exception as error:
                self._set_phase(UiPhase.ERROR)
                output.write_line(f"ERROR: compaction failed: {error}")

        elif cmd == "/new":
            self._state = self._state.with_session_id(None).with_phase(UiPhase.IDLE)
            output.write_line("session cleared")
            self._refresh_shell_chrome()

        elif cmd == "/quit":
            self.exit()

        else:
            output.write_line(f"ERROR: unknown: {cmd}")

    async def _compact_session(self) -> None:
        from just_another_coding_agent.rpc.session_store import session_path_for_id
        from just_another_coding_agent.runtime.compaction import (
            summarize_and_append_compaction_to_session,
        )

        session_path = session_path_for_id(
            sessions_root=self._sessions_root,
            session_id=self._state.session_id,
        )
        await summarize_and_append_compaction_to_session(
            model=self._state.model,
            path=session_path,
            workspace_root=self._state.workspace_root,
        )

    async def _run_prompt(self, prompt: str) -> None:
        """Run a prompt through the session-backed runtime and stream results."""
        from just_another_coding_agent.rpc.session_store import (
            create_session,
            session_path_for_id,
        )
        from just_another_coding_agent.runtime.session import (
            stream_session_run_events,
        )

        if self._state.session_id is None:
            session_id = create_session(
                sessions_root=self._sessions_root,
                workspace_root=self._state.workspace_root,
            )
            self._state = self._state.with_session_id(session_id)
            self._refresh_shell_chrome()

        session_path = session_path_for_id(
            sessions_root=self._sessions_root,
            session_id=self._state.session_id,
        )

        thinking = resolve_thinking_setting(self._state.thinking)

        output = self.query_one("#output", TranscriptLog)

        async for event in stream_session_run_events(
            model=self._state.model,
            workspace_root=self._state.workspace_root,
            session_path=session_path,
            prompt=prompt,
            thinking=thinking,
        ):
            if self._interrupt_requested:
                output.write_line("stream interrupted")
                break

            if event.type == "run_failed":
                self._set_phase(UiPhase.ERROR)

            write_stream_event(output, event)
        output.scroll_end(animate=False)
