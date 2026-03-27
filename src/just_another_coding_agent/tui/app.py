"""Main Textual application for the coding agent TUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static


class OutputPanel(Static):
    """Scrollable panel that displays streamed agent output."""

    DEFAULT_CSS = """
    OutputPanel {
        height: 1fr;
        padding: 0 1;
    }
    """


class CodingAgentApp(App[None]):
    """Interactive TUI for the coding agent."""

    TITLE = "just-another-coding-agent"

    CSS = """
    #output-scroll {
        height: 1fr;
    }
    #prompt-input {
        dock: bottom;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
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

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="output-scroll"):
            yield OutputPanel(id="output")
        yield Input(placeholder="Enter a prompt...", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._streaming:
            return

        event.input.clear()
        output = self.query_one("#output", OutputPanel)
        output.update(output.renderable + f"\n\n> {prompt}\n\n" if str(output.renderable) else f"> {prompt}\n\n")

        self._streaming = True
        try:
            await self._run_prompt(prompt)
        finally:
            self._streaming = False

    async def _run_prompt(self, prompt: str) -> None:
        """Run a prompt through the session-backed runtime and stream results."""
        from just_another_coding_agent.contracts.thinking import ThinkingSetting
        from just_another_coding_agent.runtime.session import (
            stream_session_run_events,
        )
        from just_another_coding_agent.rpc.session_store import create_session

        if self._session_id is None:
            self._session_id = create_session(
                sessions_root=self._sessions_root,
                workspace_root=self._workspace_root,
            )

        from just_another_coding_agent.rpc.session_store import session_path_for_id

        session_path = session_path_for_id(
            sessions_root=self._sessions_root,
            session_id=self._session_id,
        )

        thinking: ThinkingSetting | None = None
        if self._thinking is not None:
            if self._thinking == "true":
                thinking = True
            elif self._thinking == "false":
                thinking = False
            else:
                thinking = self._thinking  # type: ignore[assignment]

        output = self.query_one("#output", OutputPanel)
        current_text = str(output.renderable)

        async for event in stream_session_run_events(
            model=self._model,
            workspace_root=self._workspace_root,
            session_path=session_path,
            prompt=prompt,
            thinking=thinking,
        ):
            if event.type == "assistant_text_delta":
                current_text += event.delta  # type: ignore[union-attr]
                output.update(current_text)
                scroll = self.query_one("#output-scroll", VerticalScroll)
                scroll.scroll_end(animate=False)
            elif event.type == "tool_call_started":
                tool_line = f"[dim]tool: {event.tool_name}[/dim]\n"  # type: ignore[union-attr]
                current_text += tool_line
                output.update(current_text)
            elif event.type == "run_failed":
                error_line = f"\n[red]Error: {event.message}[/red]\n"  # type: ignore[union-attr]
                current_text += error_line
                output.update(current_text)
            elif event.type == "run_succeeded":
                current_text += "\n"
                output.update(current_text)
