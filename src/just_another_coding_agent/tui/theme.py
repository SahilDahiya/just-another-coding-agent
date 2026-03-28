"""Theme tokens and stylesheet builder for the JACA TUI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Named visual tokens for the one-column TUI."""

    background: str
    border: str
    border_strong: str
    text: str
    text_soft: str
    text_muted: str
    accent: str
    accent_soft: str
    success: str
    success_soft: str
    error: str


DEFAULT_THEME = ThemeTokens(
    background="#0f1115",
    border="#2a313c",
    border_strong="#4a596d",
    text="#f1ede4",
    text_soft="#ddd7cb",
    text_muted="#a7a39a",
    accent="#d79a41",
    accent_soft="#f1c27a",
    success="#7bb07c",
    success_soft="#a7d6a5",
    error="#d46a5e",
)


def build_app_css(theme: ThemeTokens = DEFAULT_THEME) -> str:
    """Build the application stylesheet from theme tokens."""
    return f"""
Screen {{
    background: transparent;
}}

StatusBar {{
    dock: top;
    height: 1;
    padding: 0;
    background: transparent;
    color: {theme.text_muted};
    border-bottom: solid {theme.border};
}}

StatusBar.phase-streaming {{
    color: {theme.accent_soft};
    border-bottom: solid {theme.accent};
}}

StatusBar.phase-compacting {{
    color: {theme.accent};
    border-bottom: solid {theme.accent_soft};
}}

StatusBar.phase-completed {{
    color: {theme.success_soft};
    border-bottom: solid {theme.success};
}}

StatusBar.phase-interrupted {{
    color: {theme.accent_soft};
    border-bottom: solid {theme.accent};
}}

StatusBar.phase-error {{
    color: {theme.error};
    border-bottom: solid {theme.error};
}}

#main {{
    height: 1fr;
    background: transparent;
}}

#output {{
    height: 1fr;
    padding: 0;
    color: {theme.text};
    background: transparent;
    border-bottom: solid {theme.border};
}}

#prompt-row {{
    dock: bottom;
    height: auto;
    padding: 0;
    background: transparent;
    border-top: solid {theme.border};
}}

#prompt-input-row {{
    height: 1;
}}

#prompt-footer {{
    height: 1;
    padding: 0;
    color: {theme.text_muted};
}}

#prompt-row.phase-streaming {{
    border-top: solid {theme.accent};
}}

#prompt-row.phase-compacting {{
    border-top: solid {theme.accent_soft};
}}

#prompt-row.phase-completed {{
    border-top: solid {theme.success};
}}

#prompt-row.phase-interrupted {{
    border-top: solid {theme.accent};
}}

#prompt-row.phase-error {{
    border-top: solid {theme.error};
}}

#prompt-footer.phase-streaming {{
    color: {theme.accent_soft};
}}

#prompt-footer.phase-compacting {{
    color: {theme.accent};
}}

#prompt-footer.phase-completed {{
    color: {theme.success_soft};
}}

#prompt-footer.phase-interrupted {{
    color: {theme.accent_soft};
}}

#prompt-footer.phase-error {{
    color: {theme.error};
}}

#prompt-marker {{
    width: 2;
    height: 1;
    color: {theme.accent};
    text-style: bold;
    padding: 0;
}}

#prompt-marker.phase-streaming {{
    color: {theme.accent_soft};
}}

#prompt-marker.phase-compacting {{
    color: {theme.accent};
}}

#prompt-marker.phase-completed {{
    color: {theme.success_soft};
}}

#prompt-marker.phase-interrupted {{
    color: {theme.accent_soft};
}}

#prompt-marker.phase-error {{
    color: {theme.error};
}}

#prompt-input {{
    width: 1fr;
    border: none;
    padding: 0;
    background: transparent;
    color: {theme.text_soft};
}}

#prompt-input.-textual-compact {{
    border: none;
    padding: 0;
    background: transparent;
    color: {theme.text_soft};
}}

#prompt-input:focus {{
    border: none;
    color: {theme.text};
    text-style: none;
    background-tint: 0%;
}}
"""


__all__ = ["DEFAULT_THEME", "ThemeTokens", "build_app_css"]
