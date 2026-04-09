package app

import (
	"strings"
	"testing"

	"jaca/internal/jaca/rpc"
)

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

func TestModelSuggestionsRequireLoadedAuthStatus(t *testing.T) {
	rows := modelSuggestions(*testModelCatalog(), nil)

	if len(rows) != 0 {
		t.Fatalf("modelSuggestions(nil authStatus) = %#v, want none", rows)
	}
}

func TestModelSuggestionsIncludeConfiguredAPIKeyAndOAuthModels(t *testing.T) {
	status := &rpc.AuthStatusResponse{
		Providers: []rpc.AuthProviderStatus{
			{Provider: "openai", Configured: true},
			{Provider: "anthropic", Configured: true},
		},
		OAuthProviders: []rpc.OAuthProviderStatus{
			{Provider: "openai-codex", LoggedIn: true},
		},
	}
	rows := modelSuggestions(*testModelCatalog(), status)

	want := map[string]string{
		"openai-responses:gpt-5.4":                    "[api-key]",
		"openai-responses:gpt-5.4-mini":               "[api-key]",
		"openai-responses:gpt-5.3-codex":              "[api-key]",
		"openai-responses:gpt-5-codex":                "[oauth]",
		"openai-responses:gpt-5-chatgpt":              "[oauth]",
		"openai-responses:gpt-5-mini-chatgpt":         "[oauth]",
		"openai-responses:gpt-5.1-chatgpt":            "[oauth]",
		"openai-responses:gpt-5.1-codex-chatgpt":      "[oauth]",
		"openai-responses:gpt-5.1-codex-mini-chatgpt": "[oauth]",
		"openai-responses:gpt-5.1-codex-max-chatgpt":  "[oauth]",
		"openai-responses:gpt-5.2-chatgpt":            "[oauth]",
		"openai-responses:gpt-5.2-codex-chatgpt":      "[oauth]",
		"openai-responses:gpt-5.3-codex-chatgpt":      "[oauth]",
		"openai-responses:gpt-5.4-chatgpt":            "[oauth]",
		"openai-responses:gpt-5.4-mini-chatgpt":       "[oauth]",
		"anthropic:claude-sonnet-4-5":                 "[api-key]",
		"anthropic:claude-opus-4-1":                   "[api-key]",
	}

	for _, row := range rows {
		label, ok := want[row.Value]
		if !ok {
			continue
		}
		if !strings.Contains(row.Description, label) {
			t.Fatalf("row %q missing label %q in %q", row.Value, label, row.Description)
		}
		delete(want, row.Value)
	}

	for value := range want {
		t.Fatalf("modelSuggestions(...) missing %q", value)
	}
}

func TestModelSuggestionsHideUnavailableModels(t *testing.T) {
	status := &rpc.AuthStatusResponse{
		Providers: []rpc.AuthProviderStatus{
			{Provider: "anthropic", Configured: true},
		},
		OAuthProviders: []rpc.OAuthProviderStatus{
			{Provider: "openai-codex", LoggedIn: false},
		},
	}
	rows := modelSuggestions(*testModelCatalog(), status)

	var sawOpenAI bool
	for _, row := range rows {
		if strings.HasPrefix(row.Value, "openai-responses:") {
			sawOpenAI = true
			if !strings.Contains(row.Description, "[oauth login required]") &&
				!strings.Contains(row.Description, "[api-key required]") {
				t.Fatalf("expected unavailable openai model to be labeled in %#v", row)
			}
		}
	}
	if !sawOpenAI {
		t.Fatalf("expected unavailable openai models to remain visible in %#v", rows)
	}
	if len(rows) == 0 {
		t.Fatal("expected non-empty model suggestions")
	}
}
