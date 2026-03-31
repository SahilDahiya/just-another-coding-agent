package app

import "testing"

func TestModelSuggestionsIncludeExpandedOllamaModels(t *testing.T) {
	rows := modelSuggestions(*testModelCatalog(), "ollama")

	want := map[string]bool{
		"ollama:kimi-k2:1t-cloud":   false,
		"ollama:glm-5:cloud":        false,
		"ollama:qwen3.5:397b-cloud": false,
		"ollama:qwen3-coder-next":   false,
	}

	for _, row := range rows {
		if _, ok := want[row.Value]; ok {
			want[row.Value] = true
		}
	}

	for value, seen := range want {
		if !seen {
			t.Fatalf("modelSuggestions(ollama) missing %q", value)
		}
	}
}

func TestModelSuggestionsIncludeGitHubModels(t *testing.T) {
	rows := modelSuggestions(*testModelCatalog(), "github")

	want := map[string]bool{
		"github:openai/gpt-5":      false,
		"github:openai/gpt-5-mini": false,
		"github:openai/gpt-4.1":    false,
	}

	for _, row := range rows {
		if _, ok := want[row.Value]; ok {
			want[row.Value] = true
		}
	}

	for value, seen := range want {
		if !seen {
			t.Fatalf("modelSuggestions(github) missing %q", value)
		}
	}
}
