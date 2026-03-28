package config

import (
	"path/filepath"
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
		APIKey:   "secret",
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
	if _, ok := got["OLLAMA_API_KEY"]; ok {
		t.Fatalf("expected OLLAMA_API_KEY to be removed, config=%v", got)
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
