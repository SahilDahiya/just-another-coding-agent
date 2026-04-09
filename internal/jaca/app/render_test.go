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
		Model:         "openai-responses:gpt-5.4",
		WorkspaceRoot: workspaceRoot,
		Thinking:      "high",
		SessionID:     "1234567890abcdef",
		MotionTick:    1,
	})

	want := "streaming.. | openai-responses:gpt-5.4 | " + displayPath(workspaceRoot) + " | thinking=high | session=12345678"
	if got != want {
		t.Fatalf("buildStatusText() = %q, want %q", got, want)
	}
}

func TestBuildPromptFooterTextShowsModelAndThinkingWhenStreaming(t *testing.T) {
	got := buildPromptFooterText(viewModel{
		Phase:    PhaseStreaming,
		Model:    "openai:gpt-5",
		Thinking: "medium",
	})

	if !strings.Contains(got, "openai:gpt-5") {
		t.Fatalf("streaming footer missing model: %q", got)
	}
	if !strings.Contains(got, "thinking=medium") {
		t.Fatalf("streaming footer missing thinking: %q", got)
	}
	if strings.Contains(got, "esc to interrupt") {
		t.Fatalf("streaming footer should not contain interrupt hint (moved to top rail): %q", got)
	}
}

func TestBuildPromptFooterTextShowsModelAndWorkspaceWhenIdle(t *testing.T) {
	got := buildPromptFooterText(viewModel{
		Phase:         PhaseIdle,
		Model:         "openai-responses:gpt-5-codex",
		WorkspaceRoot: "/workspace",
	})

	if !strings.Contains(got, "openai-responses:gpt-5-codex") {
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
		PromptFooter: "Conversation interrupted.",
		RunElapsed:   10 * time.Second,
	})

	want := "Conversation interrupted."
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
	if !strings.Contains(got, buildWorkingWave(3)) {
		t.Fatalf("buildTopRailIndicator() missing working wave: %q", got)
	}
}

func TestBuildTopRailIndicatorShowsThinkingBeforeFirstOutput(t *testing.T) {
	got := buildTopRailIndicator(viewModel{
		Phase:               PhaseStreaming,
		MotionTick:          3,
		RunElapsed:          42 * time.Second,
		AwaitingFirstOutput: true,
	})

	if !strings.Contains(got, buildThinkingWave(3)) {
		t.Fatalf("buildTopRailIndicator() missing thinking wave: %q", got)
	}
	if strings.Contains(got, buildWorkingWave(3)) {
		t.Fatalf("buildTopRailIndicator() should not show working before first output: %q", got)
	}
}

func TestBuildTopRailIndicatorShowsDetachedWorkingState(t *testing.T) {
	tick := 2
	got := buildTopRailIndicator(viewModel{
		Phase:        PhaseStreaming,
		MotionTick:   tick,
		RunElapsed:   37 * time.Second,
		DetachedLive: true,
	})

	if !strings.Contains(got, buildWorkingWave(tick)) {
		t.Fatalf("buildTopRailIndicator() missing detached live label: %q", got)
	}
	if !strings.Contains(got, "00:37") {
		t.Fatalf("buildTopRailIndicator() missing elapsed indicator: %q", got)
	}
}

func TestBuildWorkingWaveBreathesBetweenHighlights(t *testing.T) {
	if got := buildWorkingWave(0); got != "Working" {
		t.Fatalf("buildWorkingWave(0) = %q, want %q", got, "Working")
	}
	if got := buildWorkingWave(1); got != "working" {
		t.Fatalf("buildWorkingWave(1) = %q, want %q", got, "working")
	}
	if got := buildWorkingWave(2); got != "wOrking" {
		t.Fatalf("buildWorkingWave(2) = %q, want %q", got, "wOrking")
	}
	if got := buildWorkingWave(14); got != "workiNg" {
		t.Fatalf("buildWorkingWave(14) = %q, want %q", got, "workiNg")
	}
	if got := buildWorkingWave(16); got != "workIng" {
		t.Fatalf("buildWorkingWave(16) = %q, want %q", got, "workIng")
	}
	if got := buildWorkingWave(22); got != "wOrking" {
		t.Fatalf("buildWorkingWave(22) = %q, want %q", got, "wOrking")
	}
}

func TestBuildCompactingWaveBreathesAndReturns(t *testing.T) {
	if got := buildCompactingWave(0); got != "Compacting" {
		t.Fatalf("buildCompactingWave(0) = %q, want %q", got, "Compacting")
	}
	if got := buildCompactingWave(1); got != "compacting" {
		t.Fatalf("buildCompactingWave(1) = %q, want %q", got, "compacting")
	}
	if got := buildCompactingWave(2); got != "cOmpacting" {
		t.Fatalf("buildCompactingWave(2) = %q, want %q", got, "cOmpacting")
	}
	if got := buildCompactingWave(16); got != "compactiNg" {
		t.Fatalf("buildCompactingWave(16) = %q, want %q", got, "compactiNg")
	}
	if got := buildCompactingWave(20); got != "compactiNg" {
		t.Fatalf("buildCompactingWave(20) = %q, want %q", got, "compactiNg")
	}
	if got := buildCompactingWave(22); got != "compactIng" {
		t.Fatalf("buildCompactingWave(22) = %q, want %q", got, "compactIng")
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
	if !strings.Contains(rendered, buildWorkingWave(tick)) {
		t.Fatalf("renderTopRail() missing working wave: %q", rendered)
	}
}

func TestRenderTopRailShowsDetachedWorkingState(t *testing.T) {
	tick := 1
	rendered := stripANSI(renderTopRail(viewModel{
		Phase:        PhaseStreaming,
		MotionTick:   tick,
		RunElapsed:   12 * time.Second,
		DetachedLive: true,
	}))

	if !strings.Contains(rendered, buildWorkingWave(tick)) {
		t.Fatalf("renderTopRail() missing detached working label: %q", rendered)
	}
	if !strings.Contains(rendered, "00:12") {
		t.Fatalf("renderTopRail() missing elapsed indicator: %q", rendered)
	}
}

func TestRenderTopRailShowsCompactingWave(t *testing.T) {
	tick := 2
	rendered := stripANSI(renderTopRail(viewModel{
		Phase:      PhaseCompacting,
		MotionTick: tick,
		RunElapsed: 9 * time.Second,
	}))

	if !strings.Contains(rendered, buildCompactingWave(tick)) {
		t.Fatalf("renderTopRail() missing compacting wave: %q", rendered)
	}
	if !strings.Contains(rendered, "00:09") {
		t.Fatalf("renderTopRail() missing elapsed indicator: %q", rendered)
	}
}

func TestRenderLoginOverlayUsesProviderSpecificCopyForCopilot(t *testing.T) {
	rendered := stripANSI(renderLoginOverlay(viewModel{
		Width:  80,
		Height: 24,
		Login: loginOverlayView{
			Active:       true,
			Provider:     "github-copilot",
			AuthURL:      "https://github.com/login/device",
			Instructions: "Enter code: ABCD-EFGH",
			InputValue:   " ",
		},
	}))

	if !strings.Contains(rendered, "GitHub Copilot Login") {
		t.Fatalf("renderLoginOverlay() missing copilot title: %q", rendered)
	}
	if !strings.Contains(rendered, "Device-code approval completes in the browser.") {
		t.Fatalf("renderLoginOverlay() missing copilot subtitle: %q", rendered)
	}
	if strings.Contains(rendered, "Browser callback auto-completes") {
		t.Fatalf("renderLoginOverlay() should not show ChatGPT callback subtitle for copilot: %q", rendered)
	}
}

func TestRenderLoginOverlayUsesProviderSpecificCopyForChatGPT(t *testing.T) {
	rendered := stripANSI(renderLoginOverlay(viewModel{
		Width:  80,
		Height: 24,
		Login: loginOverlayView{
			Active:       true,
			Provider:     "openai-codex",
			AuthURL:      "https://auth.openai.com/oauth/authorize",
			Instructions: "Paste only if it does not return automatically.",
			InputValue:   " ",
		},
	}))

	if !strings.Contains(rendered, "ChatGPT Login") {
		t.Fatalf("renderLoginOverlay() missing ChatGPT title: %q", rendered)
	}
	if !strings.Contains(rendered, "Browser callback auto-completes") {
		t.Fatalf("renderLoginOverlay() missing ChatGPT subtitle: %q", rendered)
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
		Model:         "openai-responses:gpt-5.4-chatgpt",
	}))

	if count := strings.Count(rendered, "00:08"); count != 1 {
		t.Fatalf("renderPrompt() elapsed indicator count = %d, want 1 in %q", count, rendered)
	}
	if !strings.Contains(rendered, buildWorkingWave(tick)+" 00:08") {
		t.Fatalf("renderPrompt() missing top rail indicator: %q", rendered)
	}
}

func TestBuildPromptFooterTextShowsDetailedUsageWhenCompleted(t *testing.T) {
	input := 120
	output := 45
	total := 165
	context := 0.413

	got := buildPromptFooterText(viewModel{
		Phase: PhaseCompleted,
		Usage: usageSnapshot{
			InputTokens:   &input,
			OutputTokens:  &output,
			TotalTokens:   &total,
			ContextWindow: &context,
		},
	})

	for _, want := range []string{"completed", "59% left"} {
		if !strings.Contains(got, want) {
			t.Fatalf("buildPromptFooterText() missing %q in %q", want, got)
		}
	}
	for _, unwanted := range []string{"120 in", "45 out", "165 tok", "41% ctx"} {
		if strings.Contains(got, unwanted) {
			t.Fatalf("buildPromptFooterText() unexpectedly includes %q in %q", unwanted, got)
		}
	}
}

func TestBuildPromptFooterTextShowsCompactUsageWhenIdle(t *testing.T) {
	total := 165
	context := 0.413

	got := buildPromptFooterText(viewModel{
		Phase:         PhaseIdle,
		Model:         "openai-responses:gpt-5.4-chatgpt",
		WorkspaceRoot: "/workspace",
		Usage: usageSnapshot{
			TotalTokens:   &total,
			ContextWindow: &context,
		},
	})

	for _, want := range []string{"openai-responses:gpt-5.4-chatgpt", "/workspace", "59% left"} {
		if !strings.Contains(got, want) {
			t.Fatalf("buildPromptFooterText() missing %q in %q", want, got)
		}
	}
	for _, unwanted := range []string{"120 in", "45 out", "165 tok", "41% ctx"} {
		if strings.Contains(got, unwanted) {
			t.Fatalf("buildPromptFooterText() unexpectedly includes %q in %q", unwanted, got)
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
		Model:         "openai-responses:gpt-5.4-chatgpt",
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
		Model:         "openai-responses:gpt-5.4-chatgpt",
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
		Model:         "openai-responses:gpt-5.4-chatgpt",
		WorkspaceRoot: "/workspace",
	})

	if strings.Contains(rendered, "\x1b[") {
		t.Fatalf("renderStatus() kept ANSI escapes under Ascii profile: %q", rendered)
	}
}

func TestRenderViewShowsCenteredAuthFilePanel(t *testing.T) {
	rendered := stripANSI(renderView(viewModel{
		Width:  80,
		Height: 24,
		Auth: authOverlayView{
			Active:      true,
			Title:       "Auth File",
			Provider:    "anthropic",
			SecretLabel: "Anthropic API key",
			InputValue:  "********",
			HelpLines: []string{
				`Use API key? add "ANTHROPIC_API_KEY" to /tmp/jaca-auth.json.`,
				"OAuth also works via /login when available.",
				"Retry your prompt after saving.",
			},
		},
	}))

	for _, want := range []string{
		"Auth File",
		`Use API key? add "ANTHROPIC_API_KEY"`,
		"/tmp/jaca-auth.json",
		"Retry your prompt after saving.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("renderView() missing %q in %q", want, rendered)
		}
	}
}

func TestRenderViewShowsAuthFilePath(t *testing.T) {
	rendered := stripANSI(renderView(viewModel{
		Width:  80,
		Height: 24,
		Auth: authOverlayView{
			Active:      true,
			Title:       "Auth File",
			Provider:    "openai",
			SecretLabel: "OpenAI API key",
			InputValue:  "********",
			HelpLines: []string{
				`Use API key? add "OPENAI_API_KEY" to /tmp/jaca-auth.json.`,
				"OAuth also works via /login when available.",
				"Retry your prompt after saving.",
			},
		},
	}))

	for _, want := range []string{
		"Auth File",
		"/tmp/jaca-auth.json",
		"Retry your prompt after saving.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("renderView() missing %q in %q", want, rendered)
		}
	}
}

func TestRenderPromptShowsGroupedQueuedInputPreview(t *testing.T) {
	rendered := stripANSI(renderPrompt(viewModel{
		Phase:       PhaseStreaming,
		Width:       80,
		PromptValue: "draft",
		QueuedNext:  []string{"tighten the answer", "add tests"},
		QueuedLater: []string{"run the full suite", "summarize failures"},
	}))

	for _, want := range []string{
		"After current tool phase",
		"2 queued",
		"Esc sends now",
		"↳ tighten the answer",
		"↳ add tests",
		"At end of turn",
		"↳ run the full suite",
		"↳ summarize failures",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("renderPrompt() missing %q in %q", want, rendered)
		}
	}
}

func TestRenderViewShowsFirstRunChooserPanel(t *testing.T) {
	rendered := stripANSI(renderView(viewModel{
		Width:  80,
		Height: 24,
		Onboarding: onboardingOverlayView{
			Active:   true,
			Title:    "Get Started",
			Selected: 1,
			OptionLines: []string{
				"1. Ollama",
				"2. OpenAI",
				"3. Anthropic",
				"4. Google Gemini",
			},
			HelpLines: []string{
				"Choose a provider to get started",
				"Enter selects. Esc closes this panel.",
			},
		},
	}))

	for _, want := range []string{
		"Get Started",
		"1. Ollama",
		"2. OpenAI",
		"3. Anthropic",
		"4. Google Gemini",
		"Enter selects. Esc closes this panel.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("renderView() missing %q in %q", want, rendered)
		}
	}
}
