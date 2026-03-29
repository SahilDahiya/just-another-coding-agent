package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseBackendCommandJSON(t *testing.T) {
	raw, err := json.Marshal([]string{"python3", "-m", "just_another_coding_agent"})
	if err != nil {
		t.Fatalf("json.Marshal() error = %v", err)
	}

	command, err := parseBackendCommandJSON(string(raw))
	if err != nil {
		t.Fatalf("parseBackendCommandJSON() error = %v", err)
	}

	if len(command) != 3 {
		t.Fatalf("len(command) = %d, want 3", len(command))
	}
	if command[0] != "python3" || command[1] != "-m" || command[2] != "just_another_coding_agent" {
		t.Fatalf("command = %#v", command)
	}
}

func TestParseBackendCommandJSONFailsFast(t *testing.T) {
	tests := []string{
		"",
		"not-json",
		"[]",
		`["python3", ""]`,
	}
	for _, raw := range tests {
		if _, err := parseBackendCommandJSON(raw); err == nil {
			t.Fatalf("parseBackendCommandJSON(%q) unexpectedly succeeded", raw)
		}
	}
}

func TestRunFailsFastOnCorruptConfigJSON(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	configPath := filepath.Join(home, ".jaca", "config.json")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	if err := os.WriteFile(configPath, []byte("{not-json\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	err := run()
	if err == nil {
		t.Fatal("run() unexpectedly succeeded")
	}
	if !strings.Contains(err.Error(), configPath) {
		t.Fatalf("run() error = %q, want config path %q", err, configPath)
	}
}

func TestResolveDefaultModelPrefersConfigWhenEnvUnset(t *testing.T) {
	t.Setenv("JACA_MODEL", "")

	got := resolveDefaultModel(map[string]string{"default_model": "openai:gpt-5.4"})

	if got != "openai:gpt-5.4" {
		t.Fatalf("resolveDefaultModel() = %q, want %q", got, "openai:gpt-5.4")
	}
}

func TestResolveDefaultModelPrefersEnvOverride(t *testing.T) {
	t.Setenv("JACA_MODEL", "anthropic:claude-sonnet-4-5")

	got := resolveDefaultModel(map[string]string{"default_model": "openai:gpt-5.4"})

	if got != "anthropic:claude-sonnet-4-5" {
		t.Fatalf(
			"resolveDefaultModel() = %q, want %q",
			got,
			"anthropic:claude-sonnet-4-5",
		)
	}
}
