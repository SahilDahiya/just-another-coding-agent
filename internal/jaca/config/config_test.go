package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadFailsOnCorruptConfigJSON(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	path, err := ConfigPath()
	if err != nil {
		t.Fatalf("ConfigPath() returned error: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll() returned error: %v", err)
	}
	if err := os.WriteFile(path, []byte("{not-json\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() returned error: %v", err)
	}

	_, err = Load()
	if err == nil {
		t.Fatal("Load() unexpectedly succeeded")
	}
	if !strings.Contains(err.Error(), path) {
		t.Fatalf("Load() error = %q, want config path %q", err, path)
	}
}

func TestSaveDefaultModelPersistsSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := SaveDefaultModel("openai-responses:gpt-5.4"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if got["default_model"] != "openai-responses:gpt-5.4" {
		t.Fatalf("default_model = %q, want %q", got["default_model"], "openai-responses:gpt-5.4")
	}
}

func TestSaveDefaultProviderAcceptsAnthropic(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := SaveDefaultProvider("anthropic"); err != nil {
		t.Fatalf("SaveDefaultProvider() returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if got["default_provider"] != "anthropic" {
		t.Fatalf("default_provider = %q, want %q", got["default_provider"], "anthropic")
	}
}

func TestSaveDefaultProviderRejectsRemovedProviders(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	for _, provider := range []string{"ollama", "openrouter", "google"} {
		if err := SaveDefaultProvider(provider); err == nil {
			t.Fatalf("SaveDefaultProvider(%q) unexpectedly succeeded", provider)
		}
	}
}

func TestSaveTraceModePersistsExplicitMode(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := SaveTraceMode("local"); err != nil {
		t.Fatalf("SaveTraceMode() returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if got["trace_mode"] != "local" {
		t.Fatalf("trace_mode = %q, want %q", got["trace_mode"], "local")
	}
}

func TestSaveTraceModeRejectsUnknownMode(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := SaveTraceMode("bogus"); err == nil {
		t.Fatal("SaveTraceMode() unexpectedly succeeded")
	}
}

func TestApplyToEnvDoesNotExposeSecretKeysFromConfig(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	if err := os.Unsetenv("OPENAI_BASE_URL"); err != nil {
		t.Fatalf("Unsetenv(OPENAI_BASE_URL) returned error: %v", err)
	}
	if err := os.Unsetenv("OPENAI_API_KEY"); err != nil {
		t.Fatalf("Unsetenv(OPENAI_API_KEY) returned error: %v", err)
	}
	if err := os.Unsetenv("GOOGLE_API_KEY"); err != nil {
		t.Fatalf("Unsetenv(GOOGLE_API_KEY) returned error: %v", err)
	}

	ApplyToEnv(map[string]string{
		"OPENAI_BASE_URL": "https://example.test/v1",
		"OPENAI_API_KEY":  "should-not-apply",
		"GOOGLE_API_KEY":  "should-not-apply",
	})

	if got := os.Getenv("OPENAI_BASE_URL"); got != "https://example.test/v1" {
		t.Fatalf("OPENAI_BASE_URL = %q, want %q", got, "https://example.test/v1")
	}
	if got := os.Getenv("OPENAI_API_KEY"); got != "" {
		t.Fatalf("OPENAI_API_KEY = %q, want empty", got)
	}
	if got := os.Getenv("GOOGLE_API_KEY"); got != "" {
		t.Fatalf("GOOGLE_API_KEY = %q, want empty", got)
	}
}

func TestApplyTraceModeToEnvSetsAndClearsRuntimeEnv(t *testing.T) {
	t.Setenv("JACA_TRACE_MODE", "")

	if err := ApplyTraceModeToEnv("local"); err != nil {
		t.Fatalf("ApplyTraceModeToEnv(local) returned error: %v", err)
	}
	if got := os.Getenv("JACA_TRACE_MODE"); got != "local" {
		t.Fatalf("JACA_TRACE_MODE = %q, want %q", got, "local")
	}

	if err := ApplyTraceModeToEnv("off"); err != nil {
		t.Fatalf("ApplyTraceModeToEnv(off) returned error: %v", err)
	}
	if got := os.Getenv("JACA_TRACE_MODE"); got != "" {
		t.Fatalf("JACA_TRACE_MODE = %q, want empty", got)
	}
}
