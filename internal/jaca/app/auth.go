package app

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

type authState struct {
	Active               bool
	Provider             string
	PendingProvider      string
	PendingModel         string
	PreviousPromptFooter string
}

func (m *model) startAuthFlow(
	provider string,
	pendingProvider string,
	pendingModel string,
) {
	m.auth = authState{
		Active:               true,
		Provider:             provider,
		PendingProvider:      pendingProvider,
		PendingModel:         pendingModel,
		PreviousPromptFooter: m.promptFooterNotice,
	}
	m.textInput.SetValue("")
	m.textInput.EchoMode = textinput.EchoPassword
	m.textInput.EchoCharacter = '*'
	m.clearSlashMenu()
	m.promptFooterNotice = fmt.Sprintf(
		"auth %s  enter secret to save securely  esc to cancel",
		provider,
	)
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
	response, err := m.options.Backend.SetProviderSecret(ctx, m.auth.Provider, secret)
	if err != nil {
		m.endAuthFlow()
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}

	lines := []string{
		fmt.Sprintf(
			"%s credentials saved (%s)",
			strings.ToUpper(m.auth.Provider),
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
