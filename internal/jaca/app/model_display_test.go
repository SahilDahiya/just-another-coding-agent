package app

import "testing"

func TestDisplayModelNameUsesPublicIDAndAccessLane(t *testing.T) {
	tests := map[string]string{
		"openai-responses:gpt-5.4":                    "gpt-5.4 | api",
		"openai-responses:gpt-5.4-chatgpt":            "gpt-5.4 | oauth",
		"openai-responses:gpt-5.1-codex-mini-chatgpt": "gpt-5.1-codex-mini | oauth",
		"anthropic:claude-sonnet-4-5":                 "claude-sonnet-4-5 | api",
	}

	for input, want := range tests {
		if got := displayModelName(input); got != want {
			t.Fatalf("displayModelName(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestResolveModelSelectionAcceptsDisplayLabel(t *testing.T) {
	catalog := testModelCatalog()

	tests := map[string]string{
		"gpt-5.4 | api":           "openai-responses:gpt-5.4",
		"gpt-5.4 | oauth":         "openai-responses:gpt-5.4-chatgpt",
		"gpt-5-codex|oauth":       "openai-responses:gpt-5-codex",
		"claude-sonnet-4-5 | api": "anthropic:claude-sonnet-4-5",
	}

	for input, want := range tests {
		if got := resolveModelSelection(input, catalog); got != want {
			t.Fatalf("resolveModelSelection(%q) = %q, want %q", input, got, want)
		}
	}
}
