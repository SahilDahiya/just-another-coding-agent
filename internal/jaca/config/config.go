package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

var envKeys = []string{
	"OPENAI_API_KEY",
	"OPENAI_BASE_URL",
	"ANTHROPIC_API_KEY",
	"OLLAMA_API_KEY",
	"OLLAMA_BASE_URL",
}

func ConfigPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
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
	APIKey   string
	BaseURL  string
}

func SaveProvider(update ProviderUpdate) error {
	config, err := Load()
	if err != nil {
		return err
	}
	switch update.Provider {
	case "ollama":
		if update.BaseURL == "" {
			delete(config, "OLLAMA_BASE_URL")
			_ = os.Unsetenv("OLLAMA_BASE_URL")
		} else {
			config["OLLAMA_BASE_URL"] = update.BaseURL
			_ = os.Setenv("OLLAMA_BASE_URL", update.BaseURL)
		}
		if update.APIKey == "" {
			delete(config, "OLLAMA_API_KEY")
			_ = os.Unsetenv("OLLAMA_API_KEY")
		} else {
			config["OLLAMA_API_KEY"] = update.APIKey
			_ = os.Setenv("OLLAMA_API_KEY", update.APIKey)
		}
	case "openai":
		if update.APIKey != "" {
			config["OPENAI_API_KEY"] = update.APIKey
			_ = os.Setenv("OPENAI_API_KEY", update.APIKey)
		}
		if update.BaseURL != "" {
			config["OPENAI_BASE_URL"] = update.BaseURL
			_ = os.Setenv("OPENAI_BASE_URL", update.BaseURL)
		}
	case "anthropic":
		if update.APIKey != "" {
			config["ANTHROPIC_API_KEY"] = update.APIKey
			_ = os.Setenv("ANTHROPIC_API_KEY", update.APIKey)
		}
	default:
		return errors.New("unknown provider")
	}
	config["default_provider"] = update.Provider
	return Save(config)
}
