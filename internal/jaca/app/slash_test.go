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
