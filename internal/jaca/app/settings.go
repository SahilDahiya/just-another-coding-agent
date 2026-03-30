package app

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	"jaca/internal/jaca/config"
)

func canonicalProviderName(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "claude":
		return "anthropic"
	default:
		return strings.ToLower(strings.TrimSpace(raw))
	}
}

func providerForModel(model string) string {
	value := strings.ToLower(strings.TrimSpace(model))
	switch {
	case strings.HasPrefix(value, "openai:"):
		return "openai"
	case strings.HasPrefix(value, "anthropic:"):
		return "anthropic"
	case strings.HasPrefix(value, "ollama:"):
		return "ollama"
	default:
		return ""
	}
}

func modelMatchesProvider(model string, provider string) bool {
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(model)), provider+":")
}

func (m *model) handleModelCommand(arg string) {
	m.transcript.WriteNote("model", nil)
	value := strings.TrimSpace(arg)
	if value == "" {
		m.transcript.WriteLine(fmt.Sprintf("model: %s", m.options.Model))
		return
	}
	provider := providerForModel(value)
	if provider == "" {
		m.transcript.WriteError(fmt.Sprintf("unknown model provider: %s", value))
		return
	}
	hasCreds, err := config.HasProviderCredentials(provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	if !hasCreds {
		m.startAuthFlow(provider, provider, value)
		return
	}
	lines, restart, err := m.applyModelSelection(value, provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		m.options.Backend.SetEnv(os.Environ())
		_ = m.options.Backend.Restart(context.Background())
	}
}

func (m *model) handleTraceCommand(arg string) {
	m.transcript.WriteNote("trace", nil)
	value := strings.TrimSpace(arg)
	if value == "" {
		cfg, err := config.Load()
		if err != nil {
			m.transcript.WriteError(err.Error())
			return
		}
		mode := cfg["trace_mode"]
		if mode == "" {
			mode = "off"
		}
		m.transcript.WriteLine(fmt.Sprintf("trace: %s", mode))
		return
	}
	if err := config.SaveTraceMode(value); err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	m.transcript.WriteLine(fmt.Sprintf("trace mode set to %s", value))
	if m.options.Backend != nil {
		if err := config.ApplyTraceModeToEnv(value); err != nil {
			m.transcript.WriteError(err.Error())
			return
		}
		m.options.Backend.SetEnv(os.Environ())
		_ = m.options.Backend.Restart(context.Background())
	}
}

func (m *model) handleAuthCommand(arg string) {
	provider := canonicalProviderName(arg)
	switch provider {
	case "openai", "anthropic":
		m.startAuthFlow(provider, "", "")
	case "ollama":
		m.transcript.WriteNote("auth", nil)
		m.transcript.WriteError("manual ollama auth is not supported yet")
	default:
		m.transcript.WriteNote("auth", nil)
		m.transcript.WriteError("usage: /auth openai|anthropic")
	}
}

func (m *model) handleProviderCommand(arg string) {
	lines, restart, startAuth, err := m.handleProvider(arg)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	if startAuth != "" {
		m.startAuthFlow(startAuth, startAuth, "")
		return
	}
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		m.options.Backend.SetEnv(os.Environ())
		_ = m.options.Backend.Restart(context.Background())
	}
}

func (m *model) handleProvider(arg string) (
	lines []string,
	restart bool,
	startAuth string,
	err error,
) {
	m.transcript.WriteNote("provider", nil)
	if strings.TrimSpace(arg) == "" {
		return []string{
			"usage",
			"  /provider ollama                  select Ollama",
			"  /provider openai                  select OpenAI",
			"  /provider anthropic               select Anthropic",
			"  /auth openai                      save OpenAI API key",
			"  /auth anthropic                   save Anthropic API key",
			"",
			"provider selection is saved to ~/.jaca/config.json",
		}, false, "", nil
	}
	provider := canonicalProviderName(arg)
	switch provider {
	case "ollama":
		lines, restart, err := m.applyProviderSelection(provider)
		return lines, restart, "", err
	case "openai", "anthropic":
		hasCreds, err := config.HasProviderCredentials(provider)
		if err != nil {
			return nil, false, "", err
		}
		if !hasCreds {
			return nil, false, provider, nil
		}
		lines, restart, err := m.applyProviderSelection(provider)
		return lines, restart, "", err
	default:
		return nil, false, "", fmt.Errorf("unknown provider: %s", arg)
	}
}

func (m *model) applyProviderSelection(provider string) ([]string, bool, error) {
	if err := config.SaveDefaultProvider(provider); err != nil {
		return nil, false, err
	}

	lines := []string{fmt.Sprintf("provider set to %s", provider)}
	if !modelMatchesProvider(m.options.Model, provider) {
		nextModel, err := m.defaultModelForProvider(provider)
		if err != nil {
			return nil, false, err
		}
		if err := config.SaveDefaultModel(nextModel); err != nil {
			return nil, false, err
		}
		m.options.Model = nextModel
		if m.options.Backend != nil {
			m.options.Backend.SetModel(nextModel)
		}
		lines = append(lines, fmt.Sprintf("model set to %s", nextModel))
	}
	return lines, true, nil
}

func (m *model) defaultModelForProvider(provider string) (string, error) {
	if m.modelCatalog == nil {
		return "", errors.New("model catalog unavailable")
	}
	for _, providerCatalog := range m.modelCatalog.Providers {
		if providerCatalog.Provider == provider {
			if providerCatalog.DefaultModelID == "" {
				return "", fmt.Errorf("missing default model for provider: %s", provider)
			}
			return providerCatalog.DefaultModelID, nil
		}
	}
	return "", fmt.Errorf("unknown provider: %s", provider)
}

func (m *model) applyModelSelection(model string, provider string) ([]string, bool, error) {
	previousProvider := m.currentProvider()
	if err := config.SaveDefaultProvider(provider); err != nil {
		return nil, false, err
	}
	if err := config.SaveDefaultModel(model); err != nil {
		return nil, false, err
	}

	lines := []string{}
	if previousProvider != provider {
		lines = append(lines, fmt.Sprintf("provider set to %s", provider))
	}

	m.options.Model = model
	if m.options.Backend != nil {
		m.options.Backend.SetModel(model)
	}
	lines = append(lines, fmt.Sprintf("model set to %s", model))
	return lines, true, nil
}
