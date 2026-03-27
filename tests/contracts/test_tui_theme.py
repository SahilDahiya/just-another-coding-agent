from just_another_coding_agent.tui.theme import (
    DEFAULT_THEME,
    ThemeTokens,
    build_app_css,
)


def test_build_app_css_uses_theme_tokens() -> None:
    theme = ThemeTokens(
        background="#111111",
        surface="#222222",
        surface_alt="#333333",
        input_bg="#444444",
        input_focus_bg="#555555",
        border="#666666",
        border_strong="#777777",
        text="#888888",
        text_muted="#999999",
        accent="#aaaaaa",
        accent_soft="#bbbbbb",
        error="#cccccc",
    )

    css = build_app_css(theme)

    assert "#111111" in css
    assert "#777777" in css
    assert "#bbbbbb" in css
    assert "#prompt-input:focus" in css
    assert "StatusBar" in css


def test_default_theme_tokens_are_distinct_enough_for_hierarchy() -> None:
    assert DEFAULT_THEME.background != DEFAULT_THEME.surface
    assert DEFAULT_THEME.surface != DEFAULT_THEME.surface_alt
    assert DEFAULT_THEME.input_bg != DEFAULT_THEME.input_focus_bg
    assert DEFAULT_THEME.accent != DEFAULT_THEME.text
