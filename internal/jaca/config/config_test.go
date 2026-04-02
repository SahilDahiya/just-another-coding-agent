package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSaveProviderClearsStaleOllamaConfigAndEnv(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_BASE_URL", "")
	t.Setenv("OLLAMA_API_KEY", "")

	if err := SaveProvider(ProviderUpdate{
		Provider: "ollama",
		BaseURL:  "https://ollama.example/v1",
	}); err != nil {
		t.Fatalf("SaveProvider(set) returned error: %v", err)
	}

	if err := SaveProvider(ProviderUpdate{Provider: "ollama"}); err != nil {
		t.Fatalf("SaveProvider(clear) returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}

	if _, ok := got["OLLAMA_BASE_URL"]; ok {
		t.Fatalf("expected OLLAMA_BASE_URL to be removed, config=%v", got)
	}
	if got["default_provider"] != "ollama" {
		t.Fatalf("default_provider = %q, want %q", got["default_provider"], "ollama")
	}

	if path, err := ConfigPath(); err != nil {
		t.Fatalf("ConfigPath() returned error: %v", err)
	} else if path != filepath.Join(home, ".jaca", "config.json") {
		t.Fatalf("ConfigPath() = %q", path)
	}
}

func TestSaveProviderSetsHostedOllamaBaseURLAndEnv(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_BASE_URL", "")

	if err := SaveProvider(ProviderUpdate{
		Provider: "ollama",
		BaseURL:  OllamaCloudBaseURL,
	}); err != nil {
		t.Fatalf("SaveProvider(hosted) returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if got["OLLAMA_BASE_URL"] != OllamaCloudBaseURL {
		t.Fatalf(
			"OLLAMA_BASE_URL = %q, want %q",
			got["OLLAMA_BASE_URL"],
			OllamaCloudBaseURL,
		)
	}
	if os.Getenv("OLLAMA_BASE_URL") != OllamaCloudBaseURL {
		t.Fatalf(
			"env OLLAMA_BASE_URL = %q, want %q",
			os.Getenv("OLLAMA_BASE_URL"),
			OllamaCloudBaseURL,
		)
	}
}

func TestOllamaUsesCloudBaseURLOnlyForHostedEndpoint(t *testing.T) {
	t.Setenv("OLLAMA_BASE_URL", "")

	if OllamaUsesCloudBaseURL(map[string]string{}) {
		t.Fatal("empty config should default to local Ollama mode")
	}
	if OllamaUsesCloudBaseURL(map[string]string{"OLLAMA_BASE_URL": DefaultOllamaBaseURL}) {
		t.Fatal("local Ollama base URL should not require cloud auth")
	}
	if !OllamaUsesCloudBaseURL(map[string]string{"OLLAMA_BASE_URL": OllamaCloudBaseURL}) {
		t.Fatal("hosted Ollama base URL should require cloud auth")
	}
}

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

	if err := SaveDefaultModel("openai:gpt-5.4"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if got["default_model"] != "openai:gpt-5.4" {
		t.Fatalf("default_model = %q, want %q", got["default_model"], "openai:gpt-5.4")
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
	if err := os.Unsetenv("GITHUB_API_KEY"); err != nil {
		t.Fatalf("Unsetenv(GITHUB_API_KEY) returned error: %v", err)
	}

	ApplyToEnv(map[string]string{
		"OPENAI_BASE_URL": "https://example.test/v1",
		"OPENAI_API_KEY":  "should-not-apply",
		"GITHUB_API_KEY":  "should-not-apply",
	})

	if got := os.Getenv("OPENAI_BASE_URL"); got != "https://example.test/v1" {
		t.Fatalf("OPENAI_BASE_URL = %q, want %q", got, "https://example.test/v1")
	}
	if got := os.Getenv("OPENAI_API_KEY"); got != "" {
		t.Fatalf("OPENAI_API_KEY = %q, want empty", got)
	}
	if got := os.Getenv("GITHUB_API_KEY"); got != "" {
		t.Fatalf("GITHUB_API_KEY = %q, want empty", got)
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
