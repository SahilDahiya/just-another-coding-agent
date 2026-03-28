"""Textual widgets and stylesheet used by the interactive TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from rich.padding import Padding
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from .theme import DEFAULT_THEME, build_app_css

APP_CSS = build_app_css()


@dataclass(slots=True)
class TranscriptPart:
    """One durable transcript segment with plain-text and renderable forms."""

    renderable: object
    plain_text: str


@dataclass(slots=True)
class ToolEntry:
    """One tool row within a grouped live tool burst."""

    tool_name: str
    preview: str | None
    outcome: str | None = None
    message: str | None = None
    duration: str | None = None


@dataclass(slots=True)
class ToolGroup:
    """One live grouped tool burst rendered as a single transcript block."""

    index: int
    order: list[str]
    entries: dict[str, ToolEntry]


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
    ASSISTANT_LEFT_PAD = 2
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
    _UNORDERED_ITEM_RE = re.compile(r"^[-*+]\s+(.*)$")
    _ORDERED_ITEM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
    _INLINE_TOKEN_RE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*)")

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
        self._tool_group: ToolGroup | None = None

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

    def write_renderable(self, renderable: object, plain_text: str) -> Self:
        self.end_tool_group()
        self.flush_live_text()
        self._parts.append(TranscriptPart(renderable, plain_text))
        return super().write(renderable, scroll_end=True)

    def append_live_text(self, text: str) -> None:
        """Append streaming assistant text into one wrapped transcript block."""
        self.end_tool_group()
        if self._live_part_index is None:
            self._parts.append(
                TranscriptPart(
                    self._render_assistant_text(""),
                    "",
                )
            )
            self._live_part_index = len(self._parts) - 1
        existing_text = self._parts[self._live_part_index].plain_text
        updated_text = existing_text + text
        self._parts[self._live_part_index] = TranscriptPart(
            self._render_assistant_text(updated_text),
            updated_text,
        )
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

    def end_tool_group(self) -> None:
        """Close the current live tool burst so the next tool opens a new block."""
        self._tool_group = None

    def render_completed_assistant_markdown(self, markdown_text: str) -> None:
        """Replace the current assistant text block with a Markdown renderable."""
        self.end_tool_group()
        self.flush_live_text()
        if not markdown_text:
            self._live_part_index = None
            return

        markdown_part = TranscriptPart(
            self._render_completed_assistant(markdown_text),
            markdown_text,
        )
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
        """Append one tool entry inside a grouped live tool burst."""
        self.end_live_text()
        preview = preview.strip() if preview else None
        if self._tool_group is None:
            if self._parts and not self.plain_text.endswith("\n"):
                self._parts.append(TranscriptPart("\n", "\n"))
            self._parts.append(TranscriptPart(Text(""), ""))
            self._tool_group = ToolGroup(
                index=len(self._parts) - 1,
                order=[],
                entries={},
            )
        self._tool_group.order.append(tool_call_id)
        self._tool_group.entries[tool_call_id] = ToolEntry(
            tool_name=tool_name,
            preview=preview,
        )
        self._rewrite_tool_group()

    def finish_tool_activity(
        self,
        tool_call_id: str,
        summary: str | None = None,
        duration: str | None = None,
    ) -> None:
        """Mark one tool entry as completed successfully."""
        tool_entry = self._resolve_or_create_tool_entry(tool_call_id)
        if tool_entry is None:
            return
        tool_entry.outcome = "ok"
        tool_entry.message = summary if tool_entry.preview is None else None
        tool_entry.duration = duration
        self._rewrite_tool_group()

    def fail_tool_activity(
        self,
        tool_call_id: str,
        tool_name: str,
        message: str,
        duration: str | None = None,
    ) -> None:
        """Mark one tool entry as failed, preserving any start-row preview."""
        self.flush_live_text()
        tool_entry = self._resolve_or_create_tool_entry(
            tool_call_id,
            tool_name=tool_name,
        )
        if tool_entry is None:
            return
        tool_entry.outcome = "error"
        tool_entry.message = message
        tool_entry.duration = duration
        self._rewrite_tool_group()

    def clear(self) -> TranscriptLog:
        if self._live_flush_timer is not None:
            self._live_flush_timer.stop()
            self._live_flush_timer = None
        self._parts.clear()
        self._live_part_index = None
        self._live_dirty = False
        self._tool_group = None
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
        self.end_tool_group()
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

    def _resolve_or_create_tool_entry(
        self,
        tool_call_id: str,
        *,
        tool_name: str | None = None,
    ) -> ToolEntry | None:
        if self._tool_group is None:
            if tool_name is None:
                return None
            if self._parts and not self.plain_text.endswith("\n"):
                self._parts.append(TranscriptPart("\n", "\n"))
            self._parts.append(TranscriptPart(Text(""), ""))
            self._tool_group = ToolGroup(
                index=len(self._parts) - 1,
                order=[tool_call_id],
                entries={
                    tool_call_id: ToolEntry(tool_name=tool_name, preview=None),
                },
            )
            return self._tool_group.entries[tool_call_id]

        tool_entry = self._tool_group.entries.get(tool_call_id)
        if tool_entry is not None:
            return tool_entry
        if tool_name is None:
            return None
        self._tool_group.order.append(tool_call_id)
        self._tool_group.entries[tool_call_id] = ToolEntry(
            tool_name=tool_name,
            preview=None,
        )
        return self._tool_group.entries[tool_call_id]

    def _rewrite_tool_group(self) -> None:
        if self._tool_group is None:
            return
        renderable = Text()
        plain_parts: list[str] = []
        for tool_call_id in self._tool_group.order:
            tool_entry = self._tool_group.entries[tool_call_id]
            line = self._format_tool_activity_line(
                tool_name=tool_entry.tool_name,
                preview=tool_entry.preview,
                outcome=tool_entry.outcome,
                message=tool_entry.message,
                duration=tool_entry.duration,
            )
            renderable.append_text(
                self._render_tool_activity_line(
                    tool_name=tool_entry.tool_name,
                    preview=tool_entry.preview,
                    outcome=tool_entry.outcome,
                    message=tool_entry.message,
                    duration=tool_entry.duration,
                )
            )
            plain_parts.append(line)
        plain_text = "".join(plain_parts)
        self._parts[self._tool_group.index] = TranscriptPart(renderable, plain_text)
        self._rerender()

    @staticmethod
    def _format_tool_activity_line(
        *,
        tool_name: str,
        preview: str | None,
        outcome: str | None,
        message: str | None = None,
        duration: str | None = None,
    ) -> str:
        head = tool_name if not preview else f"{tool_name}  {preview}"
        if outcome and duration and not message:
            return f"{head}  {outcome} {duration}\n"
        if outcome and message and duration:
            return f"{head}  {outcome}  {message}  {duration}\n"
        if outcome and message:
            return f"{head}  {outcome}  {message}\n"
        if outcome:
            return f"{head}  {outcome}\n"
        if duration:
            return f"{head}  {duration}\n"
        return f"{head}\n"

    @staticmethod
    def _render_tool_activity_line(
        *,
        tool_name: str,
        preview: str | None,
        outcome: str | None,
        message: str | None = None,
        duration: str | None = None,
    ) -> Text:
        text = Text()
        text.append(tool_name, style=Style(color=DEFAULT_THEME.text_muted))
        if preview:
            text.append("  ")
            text.append(preview, style=Style(color=DEFAULT_THEME.text))
        if outcome == "ok":
            text.append("  ")
            text.append("ok", style=Style(color=DEFAULT_THEME.success_soft))
            if duration and not message:
                text.append(" ")
                text.append(
                    duration,
                    style=Style(color=DEFAULT_THEME.text_muted, dim=True),
                )
                duration = None
        elif outcome == "error":
            text.append("  ")
            text.append("error", style=Style(color=DEFAULT_THEME.error))
        if message:
            text.append("  ")
            text.append(
                message,
                style=Style(
                    color=(
                        DEFAULT_THEME.text_muted
                        if outcome != "error"
                        else DEFAULT_THEME.error
                    )
                ),
            )
        if duration:
            text.append("  ")
            text.append(
                duration,
                style=Style(color=DEFAULT_THEME.text_muted, dim=True),
            )
        text.append("\n")
        return text

    def _render_assistant_text(self, text: str) -> Padding:
        return Padding.indent(
            Text(text, style=Style(color=DEFAULT_THEME.text_soft, dim=True)),
            self.ASSISTANT_LEFT_PAD,
        )

    def _render_completed_assistant(self, markdown_text: str) -> Padding:
        text = Text()
        in_code_block = False

        for raw_line in markdown_text.splitlines():
            line = raw_line.rstrip()

            if line.startswith("```"):
                in_code_block = not in_code_block
                if text and not text.plain.endswith("\n\n"):
                    text.append("\n")
                continue

            if in_code_block:
                text.append("    ", style=Style(color=DEFAULT_THEME.text_muted))
                text.append(line, style=Style(color=DEFAULT_THEME.text))
                text.append("\n")
                continue

            if not line:
                text.append("\n")
                continue

            heading_match = self._HEADING_RE.match(line)
            if heading_match is not None:
                if text and not text.plain.endswith("\n\n"):
                    text.append("\n")
                text.append(
                    heading_match.group(2),
                    style=Style(color=DEFAULT_THEME.text_soft, bold=True),
                )
                text.append("\n")
                continue

            unordered_match = self._UNORDERED_ITEM_RE.match(line)
            if unordered_match is not None:
                text.append("    ", style=Style(color=DEFAULT_THEME.text_muted))
                self._append_inline_segments(
                    text,
                    unordered_match.group(1),
                    base_style=Style(color=DEFAULT_THEME.text_soft, dim=True),
                )
                text.append("\n")
                continue

            ordered_match = self._ORDERED_ITEM_RE.match(line)
            if ordered_match is not None:
                text.append("    ", style=Style(color=DEFAULT_THEME.text_muted))
                self._append_inline_segments(
                    text,
                    ordered_match.group(2),
                    base_style=Style(color=DEFAULT_THEME.text_soft, dim=True),
                )
                text.append("\n")
                continue

            self._append_inline_segments(
                text,
                line,
                base_style=Style(color=DEFAULT_THEME.text_soft, dim=True),
            )
            text.append("\n")

        return Padding.indent(text, self.ASSISTANT_LEFT_PAD)

    def _append_inline_segments(
        self,
        text: Text,
        content: str,
        *,
        base_style: Style,
    ) -> None:
        cursor = 0
        for match in self._INLINE_TOKEN_RE.finditer(content):
            if match.start() > cursor:
                text.append(content[cursor : match.start()], style=base_style)
            token = match.group(0)
            if token.startswith("`"):
                text.append(
                    token[1:-1],
                    style=Style(color=DEFAULT_THEME.text, bold=True),
                )
            elif token.startswith("**"):
                text.append(
                    token[2:-2],
                    style=Style(color=DEFAULT_THEME.text_soft, bold=True),
                )
            cursor = match.end()
        if cursor < len(content):
            text.append(content[cursor:], style=base_style)


__all__ = ["APP_CSS", "ComposerInput", "StatusBar", "TranscriptLog", "TranscriptPart"]
