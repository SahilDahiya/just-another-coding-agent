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
	PendingPrompt          string
	ReturnToOnboardingKind string
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
		pendingPrompt := m.auth.PendingPrompt
		m.endAuthFlow()
		m.restorePendingPrompt(pendingPrompt)
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
		pendingPrompt := m.auth.PendingPrompt
		m.endAuthFlow()
		m.restorePendingPrompt(pendingPrompt)
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}

	statuses, statusErr := m.fetchAuthStatus()
	if statusErr == nil {
		m.authStatus = &statuses
		persisted := false
		for _, status := range statuses.Providers {
			if status.Provider == response.Status.Provider {
				if !status.SecretConfigured {
					pendingPrompt := m.auth.PendingPrompt
					m.endAuthFlow()
					m.restorePendingPrompt(pendingPrompt)
					m.transcript.WriteError(
						fmt.Sprintf(
							"%s secret did not persist; current auth source is %s",
							authProviderLabel(response.Status.Provider),
							status.Source,
						),
					)
					m.refreshViewport()
					return m, nil
				}
				response.Status = status
				persisted = true
				break
			}
		}
		if !persisted {
			pendingPrompt := m.auth.PendingPrompt
			m.endAuthFlow()
			m.restorePendingPrompt(pendingPrompt)
			m.transcript.WriteError(
				fmt.Sprintf(
					"%s secret did not persist; provider status unavailable after save",
					authProviderLabel(response.Status.Provider),
				),
			)
			m.refreshViewport()
			return m, nil
		}
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
			pendingPrompt := m.auth.PendingPrompt
			m.endAuthFlow()
			m.restorePendingPrompt(pendingPrompt)
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, nil
		}
		lines = append(lines, selectedLines...)
		restart = selectedRestart
	} else if m.auth.PendingProvider != "" {
		selectedLines, selectedRestart, err := m.applyProviderSelection(m.auth.PendingProvider)
		if err != nil {
			pendingPrompt := m.auth.PendingPrompt
			m.endAuthFlow()
			m.restorePendingPrompt(pendingPrompt)
			m.transcript.WriteError(err.Error())
			m.refreshViewport()
			return m, nil
		}
		lines = append(lines, selectedLines...)
		restart = selectedRestart
	}

	pendingPrompt := m.auth.PendingPrompt
	m.endAuthFlow()
	m.restorePendingPrompt(pendingPrompt)
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		m.restartBackendWithCurrentEnv()
	}
	m.refreshViewport()
	return m, nil
}

func (m *model) restorePendingPrompt(prompt string) {
	if strings.TrimSpace(prompt) == "" {
		return
	}
	m.textInput.SetValue(prompt)
	m.textInput.CursorEnd()
	m.syncSlashMenu()
}

func authOverlayTitle(storage string) string {
	return "Auth File"
}

func authSetupLines(provider string, fileStorePath string) []string {
	return authFileSetupLines(provider, fileStorePath)
}

func authFileSetupLines(provider string, fileStorePath string) []string {
	if strings.TrimSpace(fileStorePath) == "" {
		fileStorePath = "~/.jaca/auth.json"
	}
	return []string{
		fmt.Sprintf("Use API key? add %q to %s.", authEnvKey(provider), fileStorePath),
		"OAuth also works via /login when available.",
		"Retry your prompt after saving.",
	}
}

func authProviderLabel(provider string) string {
	switch provider {
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
	case "openai":
		return "OpenAI API key"
	case "anthropic":
		return "Anthropic API key"
	default:
		return "provider secret"
	}
}

func authEnvKey(provider string) string {
	switch provider {
	case "openai":
		return "OPENAI_API_KEY"
	case "anthropic":
		return "ANTHROPIC_API_KEY"
	default:
		return "PROVIDER_API_KEY"
	}
}
