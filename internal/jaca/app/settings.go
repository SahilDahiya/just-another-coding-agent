package app

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

const authStatusTimeout = 8 * time.Second

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
	case strings.HasPrefix(value, "github:"):
		return "github"
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

func (m *model) handleModelCommand(arg string) (tea.Model, tea.Cmd) {
	m.transcript.WriteNote("model", nil)
	cmd := m.requestModelCatalog()
	value := strings.TrimSpace(arg)
	if value == "" {
		m.transcript.WriteLine(fmt.Sprintf("model: %s", m.options.Model))
		m.refreshViewport()
		return m, cmd
	}
	provider := providerForModel(value)
	if provider == "" {
		m.transcript.WriteError(fmt.Sprintf("unknown model provider: %s", value))
		m.refreshViewport()
		return m, cmd
	}
	if provider == "ollama" {
		if m.isHostedOllamaModel(value) {
			hasCreds, err := m.ollamaCloudAuthConfigured()
			if err != nil {
				m.transcript.WriteError(err.Error())
				m.refreshViewport()
				return m, cmd
			}
			if !hasCreds {
				if err := m.startCredentialSetup(provider, provider, value, "", ""); err != nil {
					m.transcript.WriteError(err.Error())
					m.refreshViewport()
					return m, cmd
				}
				m.refreshViewport()
				return m, cmd
			}
		}
	} else {
		hasCreds, err := m.providerHasCredentials(provider)
		if err != nil {
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, cmd
		}
		if !hasCreds {
			if err := m.startCredentialSetup(provider, provider, value, "", ""); err != nil {
				m.transcript.WriteError(err.Error())
				m.refreshViewport()
				return m, cmd
			}
			m.refreshViewport()
			return m, cmd
		}
	}
	lines, restart, err := m.applyModelSelection(value, provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, cmd
	}
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		_ = m.options.Backend.Restart(context.Background())
		cmd = tea.Batch(cmd, m.requestModelCatalog())
	}
	m.refreshViewport()
	return m, cmd
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
		_ = m.options.Backend.Restart(context.Background())
	}
}

func (m *model) handleAuthCommand(arg string) {
	value := strings.TrimSpace(arg)
	if value == "" {
		m.transcript.WriteNote("auth", nil)
		m.transcript.WriteError("usage: /auth <provider>|status|clear <provider>")
		return
	}
	if strings.EqualFold(value, "status") {
		m.writeAuthStatus()
		return
	}
	if provider, ok := parseClearAuthProvider(value); ok {
		m.clearProviderSecret(provider)
		return
	}

	provider := canonicalProviderName(value)
	switch provider {
	case "openai", "anthropic", "github", "ollama":
		if err := m.startCredentialSetup(provider, "", "", "", ""); err != nil {
			m.transcript.WriteNote("auth", nil)
			m.transcript.WriteError(err.Error())
		}
	default:
		m.transcript.WriteNote("auth", nil)
		m.transcript.WriteError("usage: /auth <provider>|status|clear <provider>")
	}
}

func (m *model) handleProviderCommand(arg string) {
	if canonicalProviderName(strings.TrimSpace(arg)) == "ollama" {
		m.transcript.WriteNote("provider", nil)
		m.onboarding = onboardingState{Active: true, Kind: "ollama", Selected: 0}
		return
	}
	lines, restart, startAuth, err := m.handleProvider(arg)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	if startAuth != "" {
		if err := m.startCredentialSetup(startAuth, startAuth, "", "", ""); err != nil {
			m.transcript.WriteError(err.Error())
		}
		return
	}
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
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
			"  /provider ollama                  choose local or cloud Ollama",
			"  /model ollama:<local-model>       use local Ollama with no key",
			"  /provider github                  select GitHub Models",
			"  /provider openai                  select OpenAI",
			"  /provider anthropic               select Anthropic",
			"  /auth ollama                      save Ollama cloud API key",
			"  /auth github                      save GitHub Models token",
			"  /auth openai                      save OpenAI API key",
			"  /auth anthropic                   save Anthropic API key",
			"  /auth status                      show auth source per provider",
			"  /auth clear <provider>            clear stored keychain secret",
			"",
			"provider selection is saved to ~/.jaca/config.json",
		}, false, "", nil
	}
	provider := canonicalProviderName(arg)
	switch provider {
	case "ollama":
		hasCreds, err := m.ollamaCloudConfigured()
		if err != nil {
			return nil, false, "", err
		}
		if !hasCreds {
			return nil, false, provider, nil
		}
		lines, restart, err := m.applyProviderSelection(provider)
		return lines, restart, "", err
	case "openai", "anthropic", "github":
		hasCreds, err := m.providerHasCredentials(provider)
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
	if provider == "ollama" {
		if err := config.SaveProvider(config.ProviderUpdate{
			Provider: "ollama",
			BaseURL:  config.OllamaCloudBaseURL,
		}); err != nil {
			return nil, false, err
		}
	} else {
		if err := config.SaveDefaultProvider(provider); err != nil {
			return nil, false, err
		}
	}

	lines := []string{fmt.Sprintf("provider set to %s", provider)}
	if provider == "ollama" {
		lines = append(lines, "Ollama mode set to cloud")
	}
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
	modeLine := ""
	if provider == "ollama" {
		update := config.ProviderUpdate{Provider: "ollama"}
		if m.isHostedOllamaModel(model) {
			update.BaseURL = config.OllamaCloudBaseURL
			if modeLine != "Ollama mode set to cloud" {
				modeLine = "Ollama mode set to cloud"
			}
		} else {
			modeLine = "Ollama mode set to local"
		}
		if err := config.SaveProvider(update); err != nil {
			return nil, false, err
		}
	} else {
		if err := config.SaveDefaultProvider(provider); err != nil {
			return nil, false, err
		}
	}
	if err := config.SaveDefaultModel(model); err != nil {
		return nil, false, err
	}

	lines := []string{}
	if previousProvider != provider {
		lines = append(lines, fmt.Sprintf("provider set to %s", provider))
	}
	if modeLine != "" {
		lines = append(lines, modeLine)
	}

	m.options.Model = model
	if m.options.Backend != nil {
		m.options.Backend.SetModel(model)
	}
	lines = append(lines, fmt.Sprintf("model set to %s", model))
	return lines, true, nil
}

func (m *model) providerHasCredentials(provider string) (bool, error) {
	if provider == "ollama" {
		return m.ollamaCloudConfigured()
	}
	statuses, err := m.availableAuthStatus()
	if err != nil {
		return false, err
	}
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured, nil
		}
	}
	return false, fmt.Errorf("unknown provider: %s", provider)
}

func (m *model) providerHasCredentialsFresh(provider string) (bool, error) {
	if provider == "ollama" {
		hosted, err := m.ollamaUsesHostedEndpoint()
		if err != nil {
			return false, err
		}
		if !hosted {
			return true, nil
		}
		statuses, err := m.fetchAuthStatus()
		if err != nil {
			return false, err
		}
		m.authStatus = &statuses
		for _, status := range statuses.Providers {
			if status.Provider == "ollama" {
				return status.Configured, nil
			}
		}
		return false, errors.New("missing ollama auth status")
	}
	statuses, err := m.fetchAuthStatus()
	if err != nil {
		return false, err
	}
	m.authStatus = &statuses
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured, nil
		}
	}
	return false, fmt.Errorf("unknown provider: %s", provider)
}

func (m *model) startCredentialSetup(
	provider string,
	pendingProvider string,
	pendingModel string,
	returnToOnboardingKind string,
	pendingPrompt string,
) error {
	statuses, err := m.availableAuthStatus()
	if err != nil {
		return err
	}
	for _, status := range statuses.Providers {
		if status.Provider != provider {
			continue
		}
		if !statuses.LocalSecretStore.Available {
			m.startAuthFlow(
				provider,
				"file",
				statuses.LocalSecretStore.FileStorePath,
				pendingProvider,
				pendingModel,
				pendingPrompt,
				returnToOnboardingKind,
			)
			return nil
		}
		m.startAuthFlow(
			provider,
			"keychain",
			"",
			pendingProvider,
			pendingModel,
			pendingPrompt,
			returnToOnboardingKind,
		)
		return nil
	}
	return fmt.Errorf("unknown provider: %s", provider)
}

func (m *model) ollamaCloudConfigured() (bool, error) {
	hosted, err := m.ollamaUsesHostedEndpoint()
	if err != nil {
		return false, err
	}
	if !hosted {
		return true, nil
	}
	statuses, err := m.availableAuthStatus()
	if err != nil {
		return false, err
	}
	for _, status := range statuses.Providers {
		if status.Provider == "ollama" {
			return status.Configured, nil
		}
	}
	return false, errors.New("missing ollama auth status")
}

func (m *model) ollamaCloudAuthConfigured() (bool, error) {
	statuses, err := m.availableAuthStatus()
	if err != nil {
		return false, err
	}
	for _, status := range statuses.Providers {
		if status.Provider == "ollama" {
			return status.Configured, nil
		}
	}
	return false, errors.New("missing ollama auth status")
}

func (m *model) isHostedOllamaModel(model string) bool {
	if providerForModel(model) != "ollama" || m.modelCatalog == nil {
		return false
	}
	for _, providerCatalog := range m.modelCatalog.Providers {
		if providerCatalog.Provider != "ollama" {
			continue
		}
		for _, candidate := range providerCatalog.Models {
			if candidate.ModelID == model {
				return true
			}
		}
		return false
	}
	return false
}

func (m *model) ollamaUsesHostedEndpoint() (bool, error) {
	cfg, err := config.Load()
	if err != nil {
		return false, err
	}
	return config.OllamaUsesCloudBaseURL(cfg), nil
}

func (m *model) fetchAuthStatus() (rpc.AuthStatusResponse, error) {
	if m.options.Backend == nil {
		return rpc.AuthStatusResponse{}, errors.New("backend unavailable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
	defer cancel()
	return m.options.Backend.AuthStatus(ctx)
}

func (m *model) availableAuthStatus() (rpc.AuthStatusResponse, error) {
	if m.authStatus != nil {
		return *m.authStatus, nil
	}
	statuses, err := m.fetchAuthStatus()
	if err != nil {
		return rpc.AuthStatusResponse{}, err
	}
	m.authStatus = &statuses
	return statuses, nil
}

func (m *model) writeAuthStatus() {
	m.transcript.WriteNote("auth", nil)
	statuses, err := m.fetchAuthStatus()
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	m.authStatus = &statuses
	for _, status := range statuses.Providers {
		state := "missing"
		if status.Configured {
			state = "configured"
		}
		m.transcript.WriteLine(
			fmt.Sprintf("%s: %s (%s)", status.Provider, state, status.Source),
		)
	}
	if !statuses.LocalSecretStore.Available {
		m.transcript.WriteLine("interactive auth unavailable")
		if statuses.LocalSecretStore.Message != nil && *statuses.LocalSecretStore.Message != "" {
			m.transcript.WriteLine(*statuses.LocalSecretStore.Message)
		}
	}
}

func (m *model) clearProviderSecret(provider string) {
	m.transcript.WriteNote("auth", nil)
	if m.options.Backend == nil {
		m.transcript.WriteError("backend unavailable")
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
	defer cancel()
	response, err := m.options.Backend.ClearProviderSecret(ctx, provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	statuses, statusErr := m.availableAuthStatus()
	if statusErr == nil {
		for i := range statuses.Providers {
			if statuses.Providers[i].Provider == response.Status.Provider {
				statuses.Providers[i] = response.Status
				m.authStatus = &statuses
				break
			}
		}
	}
	state := "missing"
	if response.Status.Configured {
		state = "configured"
	}
	m.transcript.WriteLine(
		fmt.Sprintf(
			"%s auth cleared; current source: %s (%s)",
			response.Status.Provider,
			state,
			response.Status.Source,
		),
	)
}

func parseClearAuthProvider(arg string) (string, bool) {
	parts := strings.Fields(strings.TrimSpace(arg))
	if len(parts) != 2 || strings.ToLower(parts[0]) != "clear" {
		return "", false
	}
	return canonicalProviderName(parts[1]), true
}
