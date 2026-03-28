"""Textual widgets and stylesheet used by the interactive TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from rich.markdown import Markdown
from textual import events
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from .theme import build_app_css

APP_CSS = build_app_css()


@dataclass(slots=True)
class TranscriptPart:
    """One durable transcript segment with plain-text and renderable forms."""

    renderable: object
    plain_text: str


@dataclass(slots=True)
class ToolRow:
    """Track one visible tool-activity row by tool call id."""

    index: int
    tool_name: str
    preview: str | None


class StatusBar(Static):
    """Top status bar showing current session state."""


class ComposerInput(Input):
    """Single-line prompt input with shell-style history bindings."""

    BINDINGS = [
        *Input.BINDINGS,
        Binding("up", "history_previous", "Previous Prompt", show=False),
        Binding("down", "history_next", "Next Prompt", show=False),
        Binding("ctrl+u", "clear_prompt", "Clear Prompt", show=False),
    ]

    def action_history_previous(self) -> None:
        self.app.action_history_previous()

    def action_history_next(self) -> None:
        self.app.action_history_next()

    def action_clear_prompt(self) -> None:
        self.app.action_clear_prompt()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.action_history_previous()
            return
        if event.key == "down":
            event.prevent_default()
            event.stop()
            self.action_history_next()
            return
        if event.key == "ctrl+u":
            event.prevent_default()
            event.stop()
            self.action_clear_prompt()
            return
        await super()._on_key(event)


class TranscriptLog(RichLog):
    """Read-only transcript log with wrapped streaming support."""

    LIVE_FLUSH_DELAY = 0.05

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            wrap=True,
            markup=False,
            auto_scroll=True,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.styles.scrollbar_size_vertical = 0
        self.styles.scrollbar_size_horizontal = 0
        self._parts: list[TranscriptPart] = []
        self._live_part_index: int | None = None
        self._live_dirty = False
        self._live_flush_timer: Timer | None = None
        self._tool_rows: dict[str, ToolRow] = {}

    can_focus = False

    @property
    def plain_text(self) -> str:
        """Return the transcript as plain text for tests and helpers."""
        return "".join(part.plain_text for part in self._parts)

    def ensure_block_gap(self) -> None:
        """Ensure the next transcript block starts after one blank separator."""
        if not self._parts:
            return
        text = self.plain_text
        if text.endswith("\n\n"):
            return
        if text.endswith("\n"):
            self.write("\n")
            return
        self.write("\n\n")

    def write_line(self, line: str) -> None:
        self.write(f"{line}\n")

    def append_live_text(self, text: str) -> None:
        """Append streaming assistant text into one wrapped transcript block."""
        if self._live_part_index is None:
            self._parts.append(TranscriptPart("", ""))
            self._live_part_index = len(self._parts) - 1
        existing_text = self._parts[self._live_part_index].plain_text
        updated_text = existing_text + text
        self._parts[self._live_part_index] = TranscriptPart(updated_text, updated_text)
        self._live_dirty = True
        if self._live_flush_timer is None:
            self._live_flush_timer = self.set_timer(
                self.LIVE_FLUSH_DELAY,
                self.flush_live_text,
                name="transcript-live-flush",
            )

    def end_live_text(self) -> None:
        """Close the current streaming assistant block, if any."""
        self.flush_live_text()
        self._live_part_index = None

    def render_completed_assistant_markdown(self, markdown_text: str) -> None:
        """Replace the current assistant text block with a Markdown renderable."""
        self.flush_live_text()
        if not markdown_text:
            self._live_part_index = None
            return

        markdown_part = TranscriptPart(Markdown(markdown_text), markdown_text)
        if self._live_part_index is not None:
            self._parts[self._live_part_index] = markdown_part
            self._rerender()
            self._live_part_index = None
            return

        self._parts.append(markdown_part)
        self._rerender()

    def start_tool_activity(
        self,
        tool_call_id: str,
        tool_name: str,
        preview: str | None = None,
    ) -> None:
        """Append one compact tool-activity row to the transcript."""
        self.end_live_text()
        preview = preview.strip() if preview else None
        if self._parts and not self.plain_text.endswith("\n"):
            self._parts.append(TranscriptPart("\n", "\n"))
        line = self._format_tool_activity_line(
            tool_name=tool_name,
            preview=preview,
            outcome=None,
        )
        self._parts.append(TranscriptPart(line, line))
        self._tool_rows[tool_call_id] = ToolRow(
            index=len(self._parts) - 1,
            tool_name=tool_name,
            preview=preview,
        )
        self._rerender()

    def finish_tool_activity(
        self,
        tool_call_id: str,
        summary: str | None = None,
    ) -> None:
        """Mark one tool row as completed successfully."""
        tool_row = self._tool_rows.pop(tool_call_id, None)
        if tool_row is None:
            return
        line = self._format_tool_activity_line(
            tool_name=tool_row.tool_name,
            preview=tool_row.preview,
            outcome="ok",
            message=summary if tool_row.preview is None else None,
        )
        self._parts[tool_row.index] = TranscriptPart(line, line)
        self._rerender()

    def fail_tool_activity(
        self,
        tool_call_id: str,
        tool_name: str,
        message: str,
    ) -> None:
        """Mark one tool row as failed, preserving any start-row preview."""
        self.flush_live_text()
        tool_row = self._tool_rows.pop(tool_call_id, None)
        preview = tool_row.preview if tool_row is not None else None
        line = self._format_tool_activity_line(
            tool_name=tool_row.tool_name if tool_row is not None else tool_name,
            preview=preview,
            outcome="error",
            message=message,
        )
        if tool_row is None:
            if self._parts and not self.plain_text.endswith("\n"):
                self._parts.append(TranscriptPart("\n", "\n"))
            self._parts.append(TranscriptPart(line, line))
        else:
            self._parts[tool_row.index] = TranscriptPart(line, line)
        self._rerender()

    def clear(self) -> TranscriptLog:
        if self._live_flush_timer is not None:
            self._live_flush_timer.stop()
            self._live_flush_timer = None
        self._parts.clear()
        self._live_part_index = None
        self._live_dirty = False
        self._tool_rows.clear()
        return super().clear()

    def write(
        self,
        content: object,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ) -> Self:
        self.flush_live_text()
        if isinstance(content, str):
            self._parts.append(TranscriptPart(content, content))
        else:
            self._parts.append(TranscriptPart(content, ""))
        return super().write(
            content,
            width=width,
            expand=expand,
            shrink=shrink,
            scroll_end=scroll_end,
            animate=animate,
        )

    def flush_live_text(self) -> None:
        """Flush any buffered streaming text into the visible transcript."""
        self._live_flush_timer = None
        if not self._live_dirty:
            return
        self._live_dirty = False
        self._rerender()

    def _rerender(self) -> None:
        super().clear()
        for part in self._parts:
            super().write(part.renderable, scroll_end=True)

    @staticmethod
    def _format_tool_activity_line(
        *,
        tool_name: str,
        preview: str | None,
        outcome: str | None,
        message: str | None = None,
    ) -> str:
        parts = [tool_name]
        if outcome is not None:
            parts.append(outcome)
        head = " ".join(parts)
        if preview and message:
            return f"{head}  {preview}  |  {message}\n"
        if preview:
            return f"{head}  {preview}\n"
        if message:
            return f"{head}  {message}\n"
        return f"{head}\n"


__all__ = ["APP_CSS", "ComposerInput", "StatusBar", "TranscriptLog", "TranscriptPart"]
