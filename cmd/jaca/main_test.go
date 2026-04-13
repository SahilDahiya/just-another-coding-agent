package main

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"jaca/internal/jaca/app"
	"jaca/internal/jaca/config"
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

	configPath, err := config.ConfigPath()
	if err != nil {
		t.Fatalf("ConfigPath() error = %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	if err := os.WriteFile(configPath, []byte("{not-json\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	err = run()
	if err == nil {
		t.Fatal("run() unexpectedly succeeded")
	}
	if !strings.Contains(err.Error(), configPath) {
		t.Fatalf("run() error = %q, want config path %q", err, configPath)
	}
}

func TestConfigureConsoleEncoding(t *testing.T) {
	if err := configureConsoleEncoding(); err != nil {
		t.Fatalf("configureConsoleEncoding() error = %v", err)
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

func TestResolveDefaultModelReturnsEmptyWithoutEnvOrConfig(t *testing.T) {
	t.Setenv("JACA_MODEL", "")

	got := resolveDefaultModel(map[string]string{})

	if got != "" {
		t.Fatalf("resolveDefaultModel() = %q, want empty", got)
	}
}

// captureStderr replaces os.Stderr with a pipe for the duration of fn and
// returns whatever was written. Used to assert that runExternalAction
// surfaces the manual-fallback message to the user.
func captureStderr(t *testing.T, fn func()) string {
	t.Helper()
	original := os.Stderr
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe() error = %v", err)
	}
	os.Stderr = writer
	defer func() { os.Stderr = original }()

	done := make(chan []byte, 1)
	go func() {
		var buf bytes.Buffer
		_, _ = io.Copy(&buf, reader)
		done <- buf.Bytes()
	}()

	fn()
	_ = writer.Close()
	captured := <-done
	_ = reader.Close()
	return string(captured)
}

func TestRunExternalActionFallsBackWhenToolMissing(t *testing.T) {
	// If the upgrade tool disappears between the Python launcher's
	// detection and the TUI exit, the user must get a copy-pastable
	// recovery command rather than a cryptic exec error.
	action := &app.ExternalAction{
		Kind:           app.ExternalActionUpdate,
		CurrentVersion: "0.1.0",
		LatestVersion:  "0.1.1",
		Command: []string{
			"jaca-nonexistent-upgrade-tool-xyz",
			"tool",
			"upgrade",
			"just-another-coding-agent",
		},
	}

	var err error
	stderr := captureStderr(t, func() {
		err = runExternalAction(action)
	})

	if err == nil {
		t.Fatal("runExternalAction() unexpectedly succeeded")
	}
	if !strings.Contains(stderr, "not found on PATH") {
		t.Errorf("stderr missing PATH-not-found hint: %q", stderr)
	}
	expectedCmdline := "jaca-nonexistent-upgrade-tool-xyz tool upgrade just-another-coding-agent"
	if !strings.Contains(stderr, expectedCmdline) {
		t.Errorf("stderr missing copy-paste command %q: %q", expectedCmdline, stderr)
	}
}

func TestRunExternalActionFallsBackOnNonZeroExit(t *testing.T) {
	// A tool that exists on PATH but exits non-zero should still surface
	// the copy-paste recovery command. /bin/false is a POSIX guarantee
	// that returns exit code 1 with no output.
	if _, err := os.Stat("/bin/false"); err != nil {
		t.Skip("/bin/false not available on this platform")
	}

	action := &app.ExternalAction{
		Kind:           app.ExternalActionUpdate,
		CurrentVersion: "0.1.0",
		LatestVersion:  "0.1.1",
		Command:        []string{"/bin/false", "tool", "upgrade", "just-another-coding-agent"},
	}

	var err error
	stderr := captureStderr(t, func() {
		err = runExternalAction(action)
	})

	if err == nil {
		t.Fatal("runExternalAction() unexpectedly succeeded")
	}
	if !strings.Contains(stderr, "Automatic upgrade failed") {
		t.Errorf("stderr missing failure prefix: %q", stderr)
	}
	if !strings.Contains(stderr, "/bin/false tool upgrade just-another-coding-agent") {
		t.Errorf("stderr missing copy-paste command: %q", stderr)
	}
}

func TestRunExternalActionEmptyCommand(t *testing.T) {
	action := &app.ExternalAction{
		Kind:    app.ExternalActionUpdate,
		Command: nil,
	}
	err := runExternalAction(action)
	if err == nil {
		t.Fatal("runExternalAction() unexpectedly succeeded")
	}
	if !strings.Contains(err.Error(), "missing external update command") {
		t.Errorf("error missing expected text: %v", err)
	}
}
