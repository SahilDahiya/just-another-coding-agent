package app

import (
	"path/filepath"
	"testing"
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

func TestBuildPromptFooterTextShowsInterruptAndEffort(t *testing.T) {
	got := buildPromptFooterText(PhaseStreaming, "medium", "")

	want := "esc to interrupt  ◐ medium · effort"
	if got != want {
		t.Fatalf("buildPromptFooterText() = %q, want %q", got, want)
	}
}

func TestBuildPromptFooterTextPreservesOverride(t *testing.T) {
	got := buildPromptFooterText(PhaseStreaming, "medium", "Conversation interrupted. Esc again to edit previous message.")

	want := "Conversation interrupted. Esc again to edit previous message."
	if got != want {
		t.Fatalf("buildPromptFooterText() = %q, want %q", got, want)
	}
}
