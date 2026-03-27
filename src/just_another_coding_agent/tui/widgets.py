"""Textual widgets and stylesheet used by the interactive TUI."""

from __future__ import annotations

from textual.timer import Timer
from textual.widgets import RichLog, Static

from .theme import build_app_css

APP_CSS = build_app_css()


class StatusBar(Static):
    """Top status bar showing current session state."""


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
        self._parts: list[str] = []
        self._live_part_index: int | None = None
        self._live_dirty = False
        self._live_flush_timer: Timer | None = None

    can_focus = False

    @property
    def plain_text(self) -> str:
        """Return the transcript as plain text for tests and helpers."""
        return "".join(self._parts)

    def write_line(self, line: str) -> None:
        self.write(f"{line}\n")

    def append_live_text(self, text: str) -> None:
        """Append streaming assistant text into one wrapped transcript block."""
        if self._live_part_index is None:
            self._parts.append("")
            self._live_part_index = len(self._parts) - 1
        self._parts[self._live_part_index] += text
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

    def clear(self) -> TranscriptLog:
        if self._live_flush_timer is not None:
            self._live_flush_timer.stop()
            self._live_flush_timer = None
        self._parts.clear()
        self._live_part_index = None
        self._live_dirty = False
        return super().clear()

    def write(self, content, *args, **kwargs):  # type: ignore[override]
        self.flush_live_text()
        if isinstance(content, str):
            self._parts.append(content)
        return super().write(content, *args, **kwargs)

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
            super().write(part, scroll_end=True)


__all__ = ["APP_CSS", "StatusBar", "TranscriptLog"]
