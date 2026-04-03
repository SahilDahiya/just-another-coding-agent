package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

var envKeys = []string{
	"OPENAI_BASE_URL",
	"OLLAMA_BASE_URL",
}

const (
	DefaultOllamaBaseURL = "http://localhost:11434/v1"
	OllamaCloudBaseURL   = "https://ollama.com/v1"
)

func ConfigPath() (string, error) {
	home := os.Getenv("HOME")
	if home == "" {
		var err error
		home, err = os.UserHomeDir()
		if err != nil {
			return "", err
		}
	}
	return filepath.Join(home, ".jaca", "config.json"), nil
}

func Load() (map[string]string, error) {
	path, err := ConfigPath()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]string{}, nil
	}
	if err != nil {
		return nil, err
	}
	config := map[string]string{}
	if err := json.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("invalid config JSON at %s: %w", path, err)
	}
	return config, nil
}

func Save(config map[string]string) error {
	path, err := ConfigPath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return err
	}
	return nil
}

func ApplyToEnv(config map[string]string) {
	for _, key := range envKeys {
		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		if value, ok := config[key]; ok && value != "" {
			_ = os.Setenv(key, value)
		}
	}
}

type ProviderUpdate struct {
	Provider string
	BaseURL  string
}

func OllamaUsesCloudBaseURL(config map[string]string) bool {
	baseURL := strings.TrimRight(strings.TrimSpace(os.Getenv("OLLAMA_BASE_URL")), "/")
	if baseURL == "" {
		baseURL = strings.TrimRight(strings.TrimSpace(config["OLLAMA_BASE_URL"]), "/")
	}
	return baseURL == strings.TrimRight(OllamaCloudBaseURL, "/")
}

func SaveOllamaBaseURL(baseURL string) error {
	config, err := Load()
	if err != nil {
		return err
	}
	if strings.TrimSpace(baseURL) == "" {
		delete(config, "OLLAMA_BASE_URL")
		_ = os.Unsetenv("OLLAMA_BASE_URL")
	} else {
		config["OLLAMA_BASE_URL"] = strings.TrimSpace(baseURL)
		_ = os.Setenv("OLLAMA_BASE_URL", strings.TrimSpace(baseURL))
	}
	return Save(config)
}

func SaveDefaultModel(model string) error {
	config, err := Load()
	if err != nil {
		return err
	}
	config["default_model"] = strings.TrimSpace(model)
	return Save(config)
}

func SaveTraceMode(mode string) error {
	switch mode {
	case "off", "local", "logfire":
	default:
		return fmt.Errorf("unknown trace mode: %s", mode)
	}
	config, err := Load()
	if err != nil {
		return err
	}
	config["trace_mode"] = mode
	return Save(config)
}

func ApplyTraceModeToEnv(mode string) error {
	switch mode {
	case "", "off":
		_ = os.Unsetenv("JACA_TRACE_MODE")
		return nil
	case "local", "logfire":
		return os.Setenv("JACA_TRACE_MODE", mode)
	default:
		return fmt.Errorf("unknown trace mode: %s", mode)
	}
}

func SaveDefaultProvider(provider string) error {
	config, err := Load()
	if err != nil {
		return err
	}
	switch provider {
	case "ollama", "openai", "anthropic", "google":
	default:
		return errors.New("unknown provider")
	}
	config["default_provider"] = provider
	return Save(config)
}

func SaveProvider(update ProviderUpdate) error {
	config, err := Load()
	if err != nil {
		return err
	}
	switch update.Provider {
	case "ollama":
		if strings.TrimSpace(update.BaseURL) == "" {
			delete(config, "OLLAMA_BASE_URL")
			_ = os.Unsetenv("OLLAMA_BASE_URL")
		} else {
			config["OLLAMA_BASE_URL"] = strings.TrimSpace(update.BaseURL)
			_ = os.Setenv("OLLAMA_BASE_URL", strings.TrimSpace(update.BaseURL))
		}
	case "openai":
		if update.BaseURL != "" {
			config["OPENAI_BASE_URL"] = update.BaseURL
			_ = os.Setenv("OPENAI_BASE_URL", update.BaseURL)
		}
	case "anthropic":
	case "google":
	default:
		return errors.New("unknown provider")
	}
	if err := Save(config); err != nil {
		return err
	}
	return SaveDefaultProvider(update.Provider)
}
