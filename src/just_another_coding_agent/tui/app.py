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
    display_path,
    resolve_thinking_setting,
    update_status_bar,
    write_startup_banner,
    write_stream_event,
)
from .widgets import APP_CSS, OutputScroll, StatusBar, TranscriptLog


class CodingAgentApp(App[None]):
    """Interactive TUI for the coding agent."""

    TITLE = "jaca"

    CSS = APP_CSS

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
        self._model = model
        self._workspace_root = workspace_root
        self._sessions_root = sessions_root
        self._thinking = thinking
        self._session_id: str | None = None
        self._streaming = False
        self._interrupt_requested = False
        self._last_interrupt_time: float = 0.0

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Vertical(id="main"):
            with OutputScroll(id="output-scroll"):
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
        self._update_status_bar()
        self.query_one("#prompt-input", Input).focus()
        output = self.query_one("#output", TranscriptLog)
        write_startup_banner(
            output,
            model=self._model,
            workspace_root=self._workspace_root,
            thinking=self._thinking,
        )

    def _update_status_bar(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        update_status_bar(
            status,
            model=self._model,
            workspace_root=self._workspace_root,
            thinking=self._thinking,
            session_id=self._session_id,
        )

    def action_interrupt(self) -> None:
        import time

        now = time.monotonic()
        if self._streaming:
            self._interrupt_requested = True
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

        self._streaming = True
        self._interrupt_requested = False
        try:
            await self._run_prompt(prompt)
        except Exception as error:
            error_msg = str(error)
            if "api_key" in error_msg.lower():
                output.write_line(f"ERROR: {error_msg}")
                output.write_line("use /login <key> to set your API key")
            else:
                output.write_line(f"ERROR: {error_msg}")
        finally:
            self._streaming = False
            self._interrupt_requested = False

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
                self._model = arg
                output.write_line(f"model set to {self._model}")
            else:
                output.write_line(f"model: {self._model}")
            self._update_status_bar()

        elif cmd == "/thinking":
            if arg:
                valid = {"true", "false", "minimal", "low", "medium", "high", "xhigh"}
                if arg.lower() in valid:
                    self._thinking = arg.lower()
                    output.write_line(f"thinking set to {self._thinking}")
                else:
                    output.write_line(
                        f"ERROR: invalid. use: {', '.join(sorted(valid))}"
                    )
            else:
                output.write_line(f"thinking: {self._thinking or 'default'}")
            self._update_status_bar()

        elif cmd == "/workspace":
            output.write_line(f"workspace: {display_path(self._workspace_root)}")

        elif cmd == "/session":
            if self._session_id:
                output.write_line(f"session: {self._session_id}")
            else:
                output.write_line("no active session")

        elif cmd == "/compact":
            if self._session_id is None:
                output.write_line("ERROR: no active session")
                return
            output.write_line("compacting...")
            try:
                await self._compact_session()
                output.write_line("session compacted")
            except Exception as error:
                output.write_line(f"ERROR: compaction failed: {error}")

        elif cmd == "/new":
            self._session_id = None
            output.write_line("session cleared")
            self._update_status_bar()

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
            session_id=self._session_id,
        )
        await summarize_and_append_compaction_to_session(
            model=self._model,
            path=session_path,
            workspace_root=self._workspace_root,
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

        if self._session_id is None:
            self._session_id = create_session(
                sessions_root=self._sessions_root,
                workspace_root=self._workspace_root,
            )
            self._update_status_bar()

        session_path = session_path_for_id(
            sessions_root=self._sessions_root,
            session_id=self._session_id,
        )

        thinking = resolve_thinking_setting(self._thinking)

        output = self.query_one("#output", TranscriptLog)

        async for event in stream_session_run_events(
            model=self._model,
            workspace_root=self._workspace_root,
            session_path=session_path,
            prompt=prompt,
            thinking=thinking,
        ):
            if self._interrupt_requested:
                output.write_line("stream interrupted")
                break

            write_stream_event(output, event)

        scroll = self.query_one("#output-scroll", OutputScroll)
        scroll.scroll_end(animate=False)
