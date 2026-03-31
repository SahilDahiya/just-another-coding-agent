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
	"OPENAI_API_KEY",
	"OPENAI_BASE_URL",
	"ANTHROPIC_API_KEY",
	"GITHUB_API_KEY",
	"OLLAMA_API_KEY",
	"OLLAMA_BASE_URL",
}

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
	APIKey   string
	BaseURL  string
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
	case "ollama", "openai", "anthropic", "github":
	default:
		return errors.New("unknown provider")
	}
	config["default_provider"] = provider
	return Save(config)
}

func HasProviderCredentials(provider string) (bool, error) {
	config, err := Load()
	if err != nil {
		return false, err
	}
	switch provider {
	case "ollama":
		return true, nil
	case "openai":
		return hasConfiguredOrEnvValue(config, "OPENAI_API_KEY"), nil
	case "anthropic":
		return hasConfiguredOrEnvValue(config, "ANTHROPIC_API_KEY"), nil
	case "github":
		return hasConfiguredOrEnvValue(config, "GITHUB_API_KEY"), nil
	default:
		return false, errors.New("unknown provider")
	}
}

func hasConfiguredOrEnvValue(config map[string]string, key string) bool {
	if strings.TrimSpace(config[key]) != "" {
		return true
	}
	return strings.TrimSpace(os.Getenv(key)) != ""
}

func SaveProvider(update ProviderUpdate) error {
	if err := SaveProviderCredentials(update); err != nil {
		return err
	}
	return SaveDefaultProvider(update.Provider)
}

func SaveProviderCredentials(update ProviderUpdate) error {
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
	case "github":
		if update.APIKey != "" {
			config["GITHUB_API_KEY"] = update.APIKey
			_ = os.Setenv("GITHUB_API_KEY", update.APIKey)
		}
	default:
		return errors.New("unknown provider")
	}
	return Save(config)
}
