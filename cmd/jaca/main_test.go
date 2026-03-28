package main

import (
	"encoding/json"
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
