from just_another_coding_agent.tui.theme import (
    DEFAULT_THEME,
    ThemeTokens,
    build_app_css,
)


def test_build_app_css_uses_theme_tokens() -> None:
    theme = ThemeTokens(
        background="#111111",
        border="#666666",
        border_strong="#777777",
        text="#888888",
        text_soft="#898989",
        text_muted="#999999",
        accent="#aaaaaa",
        accent_soft="#bbbbbb",
        success="#dddddd",
        success_soft="#eeeeee",
        error="#cccccc",
    )

    css = build_app_css(theme)

    assert "#111111" in css
    assert "#777777" in css
    assert "#bbbbbb" in css
    assert "#prompt-input:focus" in css
    assert "StatusBar" in css
    assert "StatusBar.phase-streaming" in css
    assert "StatusBar.phase-completed" in css
    assert "#prompt-row.phase-error" in css


def test_default_theme_tokens_are_distinct_enough_for_hierarchy() -> None:
    assert DEFAULT_THEME.accent != DEFAULT_THEME.text
    assert DEFAULT_THEME.success != DEFAULT_THEME.text
    assert DEFAULT_THEME.border != DEFAULT_THEME.background
    assert DEFAULT_THEME.border_strong != DEFAULT_THEME.border
