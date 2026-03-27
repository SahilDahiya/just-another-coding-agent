"""Textual widgets and stylesheet used by the interactive TUI."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Log, Static

APP_CSS = """
Screen {
    background: $surface;
}

StatusBar {
    dock: top;
    height: 1;
    padding: 0 1;
    color: $text-muted;
}

#main {
    height: 1fr;
}

#output-scroll {
    height: 1fr;
    border-top: solid $primary-darken-2;
    border-bottom: solid $primary-darken-2;
}

#output {
    height: auto;
    min-height: 1;
    padding: 1 2;
}

#prompt-row {
    dock: bottom;
    height: auto;
    padding: 0 1;
}

#prompt-marker {
    width: 2;
    height: 1;
    color: $accent;
    padding: 0;
}

#prompt-input {
    width: 1fr;
    border: none;
    padding: 0;
}

#prompt-input:focus {
    border: none;
}
"""


class StatusBar(Static):
    """Top status bar showing current session state."""


class OutputScroll(VerticalScroll):
    """Transcript container that should never steal input focus."""

    can_focus = False


class TranscriptLog(Log):
    """Read-only transcript log with line-oriented helpers."""

    can_focus = False


__all__ = ["APP_CSS", "OutputScroll", "StatusBar", "TranscriptLog"]
