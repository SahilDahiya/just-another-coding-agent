"""Textual widgets and stylesheet used by the interactive TUI."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Log, Static

from .theme import build_app_css

APP_CSS = build_app_css()


class StatusBar(Static):
    """Top status bar showing current session state."""


class OutputScroll(VerticalScroll):
    """Transcript container that should never steal input focus."""

    can_focus = False


class TranscriptLog(Log):
    """Read-only transcript log with line-oriented helpers."""

    can_focus = False


__all__ = ["APP_CSS", "OutputScroll", "StatusBar", "TranscriptLog"]
