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
    text_muted: str
    accent: str
    accent_soft: str
    error: str


DEFAULT_THEME = ThemeTokens(
    background="#0f1115",
    border="#2a313c",
    border_strong="#4a596d",
    text="#f1ede4",
    text_muted="#a7a39a",
    accent="#d79a41",
    accent_soft="#f1c27a",
    error="#d46a5e",
)


def build_app_css(theme: ThemeTokens = DEFAULT_THEME) -> str:
    """Build the application stylesheet from theme tokens."""
    return f"""
Screen {{
    background: {theme.background};
}}

StatusBar {{
    dock: top;
    height: 1;
    padding: 0 1;
    background: {theme.background};
    color: {theme.text_muted};
    border-bottom: solid {theme.border};
}}

#main {{
    height: 1fr;
    background: {theme.background};
}}

#output-scroll {{
    height: 1fr;
    background: {theme.background};
    border-top: solid {theme.border_strong};
    border-bottom: solid {theme.border};
}}

#output {{
    height: auto;
    min-height: 1;
    padding: 1 2;
    color: {theme.text};
    background: {theme.background};
}}

#prompt-row {{
    dock: bottom;
    height: auto;
    padding: 1 1 1 1;
    background: {theme.background};
    border-top: solid {theme.border};
}}

#prompt-marker {{
    width: 2;
    height: 1;
    color: {theme.accent};
    text-style: bold;
    padding: 0;
}}

#prompt-input {{
    width: 1fr;
    border: none;
    padding: 0 1;
    background: {theme.background};
    color: {theme.text};
}}

#prompt-input:focus {{
    border: none;
    color: {theme.accent_soft};
    text-style: bold;
}}
"""


__all__ = ["DEFAULT_THEME", "ThemeTokens", "build_app_css"]
