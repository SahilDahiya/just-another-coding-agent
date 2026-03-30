package app

import (
	"path/filepath"
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
	workspaceRoot := filepath.Join("workspace", "repo")
	got := buildStatusText(viewModel{
		Phase:         PhaseStreaming,
		Model:         "ollama:test",
		WorkspaceRoot: workspaceRoot,
		Thinking:      "high",
		SessionID:     "1234567890abcdef",
		MotionTick:    1,
	})

	want := "streaming.. | ollama:test | " + displayPath(workspaceRoot) + " | thinking=high | session=12345678"
	if got != want {
		t.Fatalf("buildStatusText() = %q, want %q", got, want)
	}
}

func TestBuildPromptFooterTextShowsInterruptHintAndEffort(t *testing.T) {
	got := buildPromptFooterText(viewModel{
		Phase:      PhaseStreaming,
		Thinking:   "medium",
		RunElapsed: 42 * time.Second,
	})

	if !strings.Contains(got, "esc to interrupt") {
		t.Fatalf("buildPromptFooterText() missing interrupt hint: %q", got)
	}
	if !strings.Contains(got, "◐ medium · effort") {
		t.Fatalf("buildPromptFooterText() missing effort: %q", got)
	}
	if strings.Contains(got, "42s") || strings.Contains(got, "00:42") {
		t.Fatalf("buildPromptFooterText() should not include elapsed once top rail owns it: %q", got)
	}
}

func TestBuildPromptFooterTextShowsModelAndWorkspaceWhenIdle(t *testing.T) {
	got := buildPromptFooterText(viewModel{
		Phase:         PhaseIdle,
		Model:         "ollama:kimi-k2:1t-cloud",
		WorkspaceRoot: "/workspace",
	})

	if !strings.Contains(got, "ollama:kimi-k2:1t-cloud") {
		t.Fatalf("idle footer missing model: %q", got)
	}
	if !strings.Contains(got, "/workspace") {
		t.Fatalf("idle footer missing workspace: %q", got)
	}
}

func TestBuildPromptFooterTextPreservesOverride(t *testing.T) {
	got := buildPromptFooterText(viewModel{
		Phase:        PhaseStreaming,
		Thinking:     "medium",
		PromptFooter: "Conversation interrupted. Esc again to edit previous message.",
		RunElapsed:   10 * time.Second,
	})

	want := "Conversation interrupted. Esc again to edit previous message."
	if got != want {
		t.Fatalf("buildPromptFooterText() = %q, want %q", got, want)
	}
}

func TestBuildTopRailIndicatorShowsFixedElapsed(t *testing.T) {
	got := buildTopRailIndicator(viewModel{
		Phase:      PhaseStreaming,
		MotionTick: 3,
		RunElapsed: 42 * time.Second,
	})

	if !strings.Contains(got, "00:42") {
		t.Fatalf("buildTopRailIndicator() missing fixed elapsed: %q", got)
	}
	if !strings.Contains(got, topRailFrames[3]) {
		t.Fatalf("buildTopRailIndicator() missing braille frame: %q", got)
	}
}

func TestBuildTopRailIndicatorHiddenWhenIdle(t *testing.T) {
	got := buildTopRailIndicator(viewModel{
		Phase:      PhaseIdle,
		MotionTick: 3,
		RunElapsed: 42 * time.Second,
	})

	if got != "" {
		t.Fatalf("buildTopRailIndicator() = %q, want empty for idle", got)
	}
}

func TestRenderPromptRuleShowsTopRailIndicatorDuringStreaming(t *testing.T) {
	tick := 2
	rendered := stripANSI(renderTopRail(viewModel{
		Phase:      PhaseStreaming,
		MotionTick: tick,
		RunElapsed: 37 * time.Second,
	}))

	if !strings.Contains(rendered, "00:37") {
		t.Fatalf("renderTopRail() missing elapsed indicator: %q", rendered)
	}
	frame := topRailFrames[tick%len(topRailFrames)]
	if !strings.Contains(rendered, frame) {
		t.Fatalf("renderTopRail() missing spinner frame: %q", rendered)
	}
}

func TestRenderPromptRuleStaysPlainWhenIdle(t *testing.T) {
	rendered := stripANSI(renderPromptRule(12, defaultTheme.border))

	if rendered != strings.Repeat("─", 12) {
		t.Fatalf("idle rule = %q, want plain rule", rendered)
	}
}

func TestRenderPromptShowsSingleTopRailIndicator(t *testing.T) {
	tick := 2
	rendered := stripANSI(renderPrompt(viewModel{
		Phase:         PhaseStreaming,
		MotionTick:    tick,
		RunElapsed:    8 * time.Second,
		Transcript:    "hello",
		PromptValue:   "",
		PromptFooter:  "",
		Thinking:      "medium",
		WorkspaceRoot: "/workspace",
		Model:         "ollama:test",
	}))

	if count := strings.Count(rendered, "00:08"); count != 1 {
		t.Fatalf("renderPrompt() elapsed indicator count = %d, want 1 in %q", count, rendered)
	}
	frame := topRailFrames[tick%len(topRailFrames)]
	if !strings.Contains(rendered, frame+" 00:08") {
		t.Fatalf("renderPrompt() missing top rail indicator: %q", rendered)
	}
}

func TestBuildPromptFooterTextShowsDetailedUsageWhenCompleted(t *testing.T) {
	input := 120
	output := 45
	total := 165
	context := 0.413

	got := buildPromptFooterText(viewModel{
		Phase:         PhaseCompleted,
		InputTokens:   &input,
		OutputTokens:  &output,
		TotalTokens:   &total,
		ContextWindow: &context,
	})

	for _, want := range []string{"completed", "120 in", "45 out", "165 tok", "41% ctx"} {
		if !strings.Contains(got, want) {
			t.Fatalf("buildPromptFooterText() missing %q in %q", want, got)
		}
	}
}

func TestBuildPromptFooterTextShowsCompactUsageWhenIdle(t *testing.T) {
	total := 165
	context := 0.413

	got := buildPromptFooterText(viewModel{
		Phase:         PhaseIdle,
		Model:         "ollama:kimi-k2:1t-cloud",
		WorkspaceRoot: "/workspace",
		TotalTokens:   &total,
		ContextWindow: &context,
	})

	for _, want := range []string{"ollama:kimi-k2:1t-cloud", "/workspace", "165 tok", "41% ctx"} {
		if !strings.Contains(got, want) {
			t.Fatalf("buildPromptFooterText() missing %q in %q", want, got)
		}
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
