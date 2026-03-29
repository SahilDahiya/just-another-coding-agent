package app

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

func TestDisplayPathUsesHomeRelativePrefix(t *testing.T) {
	original := osUserHomeDir
	t.Cleanup(func() {
		osUserHomeDir = original
	})
	osUserHomeDir = func() (string, error) {
		return "/home/tester", nil
	}

	got := displayPath("/home/tester/work/repo")
	if got != "~/work/repo" {
		t.Fatalf("displayPath() = %q, want %q", got, "~/work/repo")
	}
}

func TestBuildStatusTextIncludesTruncatedSessionAndThinking(t *testing.T) {
	got := buildStatusText(viewModel{
		Phase:         PhaseStreaming,
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
		Thinking:      "high",
		SessionID:     "1234567890abcdef",
		MotionTick:    1,
	})

	want := "streaming.. | ollama:test | /workspace | thinking=high | session=12345678"
	if got != want {
		t.Fatalf("buildStatusText() = %q, want %q", got, want)
	}
}

func TestBuildPromptFooterTextShowsElapsedAndEffort(t *testing.T) {
	got := buildPromptFooterText(PhaseStreaming, "medium", "", 42*time.Second)

	if !strings.Contains(got, "42s") {
		t.Fatalf("buildPromptFooterText() missing elapsed: %q", got)
	}
	if !strings.Contains(got, "esc to interrupt") {
		t.Fatalf("buildPromptFooterText() missing interrupt hint: %q", got)
	}
	if !strings.Contains(got, "◐ medium · effort") {
		t.Fatalf("buildPromptFooterText() missing effort: %q", got)
	}
}

func TestBuildPromptFooterTextPreservesOverride(t *testing.T) {
	got := buildPromptFooterText(PhaseStreaming, "medium", "Conversation interrupted. Esc again to edit previous message.", 10*time.Second)

	want := "Conversation interrupted. Esc again to edit previous message."
	if got != want {
		t.Fatalf("buildPromptFooterText() = %q, want %q", got, want)
	}
}

func TestRenderStatusUsesTrueColorPaletteWhenAvailable(t *testing.T) {
	original := lipgloss.ColorProfile()
	t.Cleanup(func() {
		lipgloss.SetColorProfile(original)
	})
	lipgloss.SetColorProfile(termenv.TrueColor)

	rendered := renderStatus(viewModel{
		Phase:         PhaseStreaming,
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
	})

	if !strings.Contains(rendered, "38;2;") {
		t.Fatalf("renderStatus() missing truecolor escape sequence: %q", rendered)
	}
}

func TestRenderStatusUsesAnsiPaletteWithoutTrueColorEscapes(t *testing.T) {
	original := lipgloss.ColorProfile()
	t.Cleanup(func() {
		lipgloss.SetColorProfile(original)
	})
	lipgloss.SetColorProfile(termenv.ANSI)

	rendered := renderStatus(viewModel{
		Phase:         PhaseStreaming,
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
	})

	if !strings.Contains(rendered, "\x1b[") {
		t.Fatalf("renderStatus() missing ANSI styling: %q", rendered)
	}
	if strings.Contains(rendered, "38;2;") || strings.Contains(rendered, "38;5;") {
		t.Fatalf("renderStatus() used higher color profile escapes under ANSI: %q", rendered)
	}
}

func TestRenderStatusDropsColorInAsciiProfile(t *testing.T) {
	original := lipgloss.ColorProfile()
	t.Cleanup(func() {
		lipgloss.SetColorProfile(original)
	})
	lipgloss.SetColorProfile(termenv.Ascii)

	rendered := renderStatus(viewModel{
		Phase:         PhaseStreaming,
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
	})

	if strings.Contains(rendered, "\x1b[") {
		t.Fatalf("renderStatus() kept ANSI escapes under Ascii profile: %q", rendered)
	}
}
