package app

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

const authStatusTimeout = 30 * time.Second
const authLoginWaitTimeout = 16 * time.Minute

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
	case strings.HasPrefix(value, "openai-responses:"):
		return "openai"
	case strings.HasPrefix(value, "openai-chat:"):
		return "openai"
	case strings.HasPrefix(value, "anthropic:"):
		return "anthropic"
	default:
		return ""
	}
}

func isOpenAICodexOAuthModel(model string) bool {
	value := strings.ToLower(strings.TrimSpace(model))
	return value == "openai-responses:gpt-5-codex" ||
		(strings.HasPrefix(value, "openai-responses:") && strings.HasSuffix(value, "-chatgpt"))
}

func modelMatchesProvider(model string, provider string) bool {
	return providerForModel(model) == provider
}

func (m *model) handleModelCommand(arg string) (tea.Model, tea.Cmd) {
	value := strings.TrimSpace(arg)

	m.transcript.WriteNote("model", nil)
	cmd := m.requestModelCatalog()
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
	if isOpenAICodexOAuthModel(value) {
		loggedIn, err := m.openAICodexLoggedIn()
		if err != nil {
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, cmd
		}
		if !loggedIn {
			return m.startOpenAICodexLoginFlow(value, "")
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
	m.options.Thinking = "medium"
	m.transcript.WriteLine("thinking: medium (default)")
	if restart && m.options.Backend != nil {
		m.restartBackendWithCurrentEnv()
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
		m.restartBackendWithCurrentEnv()
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
	case "openai", "anthropic":
		if err := m.startCredentialSetup(provider, "", "", "", ""); err != nil {
			m.transcript.WriteNote("auth", nil)
			m.transcript.WriteError(err.Error())
		}
	default:
		m.transcript.WriteNote("auth", nil)
		m.transcript.WriteError("usage: /auth <provider>|status|clear <provider>")
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

func (m *model) providerHasCredentials(provider string) (bool, error) {
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

func (m *model) openAICodexLoggedIn() (bool, error) {
	statuses, err := m.availableAuthStatus()
	if err != nil {
		return false, err
	}
	for _, status := range statuses.OAuthProviders {
		if status.Provider == "openai-codex" {
			return status.LoggedIn, nil
		}
	}
	return false, nil
}

func (m *model) providerHasCredentialsFresh(provider string) (bool, error) {
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

func (m *model) openAICodexLoggedInFresh() (bool, error) {
	statuses, err := m.fetchAuthStatus()
	if err != nil {
		return false, err
	}
	m.authStatus = &statuses
	for _, status := range statuses.OAuthProviders {
		if status.Provider == "openai-codex" {
			return status.LoggedIn, nil
		}
	}
	return false, nil
}

func (m *model) restartBackendWithCurrentEnv() {
	if m.options.Backend == nil {
		return
	}
	m.options.Backend.SetEnv(os.Environ())
	_ = m.options.Backend.Restart(context.Background())
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
		m.transcript.WriteNote(
			"auth",
			authFileSetupLines(provider, statuses.LocalSecretStore.FileStorePath),
		)
		m.auth.PendingProvider = pendingProvider
		m.auth.PendingModel = pendingModel
		m.auth.PendingPrompt = pendingPrompt
		m.auth.ReturnToOnboardingKind = returnToOnboardingKind
		return nil
	}
	return fmt.Errorf("unknown provider: %s", provider)
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
		if status.Configured && status.Reason == "local_endpoint_no_secret_required" {
			state = "configured (no secret required)"
		}
		m.transcript.WriteLine(
			fmt.Sprintf("%s: %s (%s)", status.Provider, state, status.Source),
		)
	}
	for _, status := range statuses.OAuthProviders {
		state := "logged out"
		if status.LoggedIn {
			state = "logged in"
		}
		line := fmt.Sprintf("%s: %s", status.Provider, state)
		if status.AccountID != nil && *status.AccountID != "" {
			line = fmt.Sprintf("%s (%s)", line, *status.AccountID)
		}
		m.transcript.WriteLine(line)
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
