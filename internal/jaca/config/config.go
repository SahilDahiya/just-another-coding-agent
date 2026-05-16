package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var envKeys = []string{
	"OPENAI_BASE_URL",
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
	rawConfig, err := loadRawConfig(path)
	if err != nil {
		return nil, err
	}
	config := map[string]string{}
	for key, rawValue := range rawConfig {
		value, ok := rawStringValue(rawValue)
		if ok {
			config[key] = value
		}
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
	rawConfig, err := loadRawConfig(path)
	if err != nil {
		return err
	}
	merged := map[string]json.RawMessage{}
	for key, rawValue := range rawConfig {
		_, ok := rawStringValue(rawValue)
		if !ok {
			merged[key] = rawValue
		}
	}
	for key, value := range config {
		rawValue, err := json.Marshal(value)
		if err != nil {
			return err
		}
		merged[key] = rawValue
	}
	data, err := json.MarshalIndent(merged, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return err
	}
	return nil
}

func loadRawConfig(path string) (map[string]json.RawMessage, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]json.RawMessage{}, nil
	}
	if err != nil {
		return nil, err
	}
	rawConfig := map[string]json.RawMessage{}
	if err := json.Unmarshal(data, &rawConfig); err != nil {
		return nil, fmt.Errorf("invalid config JSON at %s: %w", path, err)
	}
	return rawConfig, nil
}

func rawStringValue(rawValue json.RawMessage) (string, bool) {
	var value string
	if err := json.Unmarshal(rawValue, &value); err == nil {
		return value, true
	}
	return "", false
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
	case "openai", "anthropic":
	default:
		return errors.New("unknown provider")
	}
	config["default_provider"] = provider
	return Save(config)
}

func SaveUpdateSnoozeUntil(until time.Time) error {
	config, err := Load()
	if err != nil {
		return err
	}
	if until.IsZero() {
		delete(config, "update_snooze_until")
	} else {
		config["update_snooze_until"] = until.UTC().Format(time.RFC3339)
	}
	return Save(config)
}

func SaveSkippedUpdateVersion(version string) error {
	config, err := Load()
	if err != nil {
		return err
	}
	version = strings.TrimSpace(version)
	if version == "" {
		delete(config, "update_skip_version")
	} else {
		config["update_skip_version"] = version
	}
	return Save(config)
}
