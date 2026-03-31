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
	PendingProvider        string
	PendingModel           string
	ReturnToOnboardingKind string
}

type authUnavailableState struct {
	Active                 bool
	Provider               string
	EnvKey                 string
	Message                string
	ReturnToOnboardingKind string
}

func (m *model) startAuthFlow(
	provider string,
	pendingProvider string,
	pendingModel string,
	returnToOnboardingKind string,
) {
	m.authUnavailable = authUnavailableState{}
	m.auth = authState{
		Active:                 true,
		Provider:               provider,
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

func (m *model) startAuthUnavailableFlow(
	provider string,
	envKey string,
	message string,
	returnToOnboardingKind string,
) {
	m.endAuthFlow()
	m.authUnavailable = authUnavailableState{
		Active:                 true,
		Provider:               provider,
		EnvKey:                 envKey,
		Message:                message,
		ReturnToOnboardingKind: returnToOnboardingKind,
	}
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

func (m *model) endAuthUnavailableFlow() {
	m.authUnavailable = authUnavailableState{}
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
	response, err := m.options.Backend.SetProviderSecret(ctx, m.auth.Provider, secret)
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

func (m *model) handleAuthUnavailableKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "enter":
		returnKind := m.authUnavailable.ReturnToOnboardingKind
		provider := m.authUnavailable.Provider
		m.endAuthUnavailableFlow()
		if returnKind != "" {
			m.onboarding = onboardingState{
				Active:   true,
				Kind:     returnKind,
				Selected: onboardingSelectionForProvider(provider),
			}
		}
		m.refreshViewport()
		return m, nil
	default:
		return m, nil
	}
}

func authSetupLines(provider string) []string {
	return []string{
		fmt.Sprintf("Enter your %s", authSecretLabel(provider)),
		"Stored in the OS keychain",
		"Not added to transcript or prompt history",
		"Enter saves. Esc cancels.",
	}
}

func authUnavailableLines(provider string, envKey string, message string) []string {
	lines := []string{
		fmt.Sprintf("Interactive secure setup is unavailable for %s.", authProviderLabel(provider)),
	}
	if strings.TrimSpace(message) != "" {
		lines = append(lines, message)
	}
	if strings.TrimSpace(envKey) != "" {
		lines = append(lines, fmt.Sprintf("Set %s in your environment and relaunch JACA.", envKey))
	}
	lines = append(lines, fmt.Sprintf("Or configure a system keychain and retry /auth %s.", provider))
	lines = append(lines, "Enter closes. Esc goes back.")
	return lines
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
