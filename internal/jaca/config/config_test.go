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
