package app

import "testing"

func TestSlashCommandSuggestionsComeFromRegistry(t *testing.T) {
	rows := slashCommandSuggestions()
	if len(rows) != len(slashCommands) {
		t.Fatalf("len(rows) = %d, want %d", len(rows), len(slashCommands))
	}
	for i, command := range slashCommands {
		if rows[i].Value != command.Value {
			t.Fatalf("rows[%d].Value = %q, want %q", i, rows[i].Value, command.Value)
		}
		if rows[i].Description != command.Description {
			t.Fatalf("rows[%d].Description = %q, want %q", i, rows[i].Description, command.Description)
		}
		if rows[i].AcceptsArgs != command.AcceptsArgs {
			t.Fatalf("rows[%d].AcceptsArgs = %v, want %v", i, rows[i].AcceptsArgs, command.AcceptsArgs)
		}
	}
}

func TestBuildSlashMenuStateUsesRegistryArgumentSuggestions(t *testing.T) {
	m := newTestModel()
	state := buildSlashMenuState("/trace loc", m)
	if state.Mode != slashMenuArguments {
		t.Fatalf("state.Mode = %q, want %q", state.Mode, slashMenuArguments)
	}
	if state.Command != "/trace" {
		t.Fatalf("state.Command = %q, want %q", state.Command, "/trace")
	}
	if len(state.Rows) != 1 || state.Rows[0].Value != "local" {
		t.Fatalf("state.Rows = %#v, want one local trace suggestion", state.Rows)
	}
}

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
