package app

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

type authState struct {
	Active                 bool
	Provider               string
	Storage                string
	FileStorePath          string
	PendingProvider        string
	PendingModel           string
	ReturnToOnboardingKind string
}

func (m *model) startAuthFlow(
	provider string,
	storage string,
	fileStorePath string,
	pendingProvider string,
	pendingModel string,
	returnToOnboardingKind string,
) {
	m.auth = authState{
		Active:                 true,
		Provider:               provider,
		Storage:                storage,
		FileStorePath:          fileStorePath,
		PendingProvider:        pendingProvider,
		PendingModel:           pendingModel,
		ReturnToOnboardingKind: returnToOnboardingKind,
	}
	m.textInput.SetValue("")
	m.textInput.EchoMode = textinput.EchoPassword
	m.textInput.EchoCharacter = '*'
	m.clearSlashMenu()
	m.promptFooterNotice = ""
}

func (m *model) endAuthFlow() {
	m.auth = authState{}
	m.textInput.EchoMode = textinput.EchoNormal
	m.textInput.EchoCharacter = '*'
	m.textInput.SetValue("")
	m.promptFooterNotice = ""
	m.syncSlashMenu()
}

func (m *model) promptDisplayValue() string {
	if !m.auth.Active {
		return m.textInput.Value()
	}
	if m.textInput.Value() == "" {
		return ""
	}
	return strings.Repeat(string(m.textInput.EchoCharacter), len([]rune(m.textInput.Value())))
}

func (m *model) promptView() string {
	if m.auth.Active {
		return m.promptDisplayValue()
	}
	return m.textInput.View()
}

func (m *model) handleAuthEnter() (tea.Model, tea.Cmd) {
	secret := strings.TrimSpace(m.textInput.Value())
	if secret == "" {
		return m, nil
	}

	if m.options.Backend == nil {
		m.endAuthFlow()
		m.transcript.WriteError("backend unavailable")
		m.refreshViewport()
		return m, nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
	defer cancel()
	response, err := m.options.Backend.SetProviderSecret(
		ctx,
		m.auth.Provider,
		secret,
		m.auth.Storage,
	)
	if err != nil {
		m.endAuthFlow()
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}
	statuses, statusErr := m.availableAuthStatus()
	if statusErr == nil {
		updated := false
		for i := range statuses.Providers {
			if statuses.Providers[i].Provider == response.Status.Provider {
				statuses.Providers[i] = response.Status
				updated = true
				break
			}
		}
		if !updated {
			statuses.Providers = append(statuses.Providers, response.Status)
		}
		m.authStatus = &statuses
	}

	lines := []string{
		fmt.Sprintf(
			"%s configured (%s)",
			authProviderLabel(m.auth.Provider),
			response.Status.Source,
		),
	}
	restart := false
	if m.auth.PendingModel != "" {
		selectedLines, selectedRestart, err := m.applyModelSelection(
			m.auth.PendingModel,
			m.auth.PendingProvider,
		)
		if err != nil {
			m.endAuthFlow()
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, nil
		}
		lines = append(lines, selectedLines...)
		restart = selectedRestart
	} else if m.auth.PendingProvider != "" {
		selectedLines, selectedRestart, err := m.applyProviderSelection(m.auth.PendingProvider)
		if err != nil {
			m.endAuthFlow()
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, nil
		}
		lines = append(lines, selectedLines...)
		restart = selectedRestart
	}

	m.endAuthFlow()
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		_ = m.options.Backend.Restart(context.Background())
	}
	m.refreshViewport()
	return m, nil
}

func authSetupLines(provider string) []string {
	return authSetupLinesForStorage(provider, "keychain", "")
}

func authOverlayTitle(storage string) string {
	if storage == "file" {
		return "Local Secret File"
	}
	return "Secure Setup"
}

func authSetupLinesForStorage(provider string, storage string, fileStorePath string) []string {
	if storage == "file" {
		return []string{
			fmt.Sprintf("Enter your %s", authSecretLabel(provider)),
			"OS keychain unavailable; using local secret file instead",
			fmt.Sprintf("Stored in %s", fileStorePath),
			"Enter saves. Esc cancels.",
		}
	}
	return []string{
		fmt.Sprintf("Enter your %s", authSecretLabel(provider)),
		"Stored in the OS keychain",
		"Not added to transcript or prompt history",
		"Enter saves. Esc cancels.",
	}
}

func authProviderLabel(provider string) string {
	switch provider {
	case "ollama":
		return "Ollama"
	case "github":
		return "GitHub"
	case "openai":
		return "OpenAI"
	case "anthropic":
		return "Anthropic"
	default:
		return strings.ToUpper(provider)
	}
}

func authSecretLabel(provider string) string {
	switch provider {
	case "ollama":
		return "Ollama cloud API key"
	case "github":
		return "GitHub Models token"
	case "openai":
		return "OpenAI API key"
	case "anthropic":
		return "Anthropic API key"
	default:
		return "provider secret"
	}
}
