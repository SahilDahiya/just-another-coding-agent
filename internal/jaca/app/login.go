package app

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"runtime"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/rpc"
)

type loginState struct {
	Active        bool
	Provider      string
	FlowID        string
	AuthURL       string
	Instructions  string
	Waiting       bool
	PendingModel  string
	PendingPrompt string
}

type startOpenAICodexLoginMsg struct {
	Response rpc.AuthLoginOpenAICodexStartResponse
	Err      error
}

type completeOpenAICodexLoginMsg struct {
	Response rpc.AuthLoginOpenAICodexCompleteResponse
	Err      error
}

type waitOpenAICodexLoginMsg struct {
	Response rpc.AuthLoginOpenAICodexWaitResponse
	Err      error
}

func (m *model) startOpenAICodexLoginFlow(pendingModel string, pendingPrompt string) (tea.Model, tea.Cmd) {
	if m.options.Backend == nil {
		m.transcript.WriteError("backend unavailable")
		m.refreshViewport()
		return m, nil
	}
	m.login = loginState{
		Provider:      "openai-codex",
		PendingModel:  pendingModel,
		PendingPrompt: pendingPrompt,
	}
	m.textInput.EchoMode = textinput.EchoNormal
	m.textInput.SetValue("")
	m.textInput.CursorEnd()
	m.clearSlashMenu()
	m.promptFooterNotice = ""
	m.refreshViewport()
	return m, startOpenAICodexLogin(m.options.Backend)
}

func (m *model) endLoginFlow() {
	m.login = loginState{}
	m.textInput.EchoMode = textinput.EchoNormal
	m.textInput.SetValue("")
	m.promptFooterNotice = ""
	m.syncSlashMenu()
}

func (m *model) handleLoginEnter() (tea.Model, tea.Cmd) {
	value := strings.TrimSpace(m.textInput.Value())
	if value == "" {
		return m, nil
	}
	return m.submitOpenAICodexLoginCompletion(value)
}

func (m *model) handleLoginCommand(arg string) (tea.Model, tea.Cmd) {
	value := strings.TrimSpace(arg)
	lowered := strings.ToLower(value)
	if lowered == "" {
		m.transcript.WriteNote("login", loginUsageLines())
		m.refreshViewport()
		return m, nil
	}
	if lowered == "openai" || lowered == "anthropic" {
		if err := m.startCredentialSetup(lowered, "", "", "", ""); err != nil {
			m.transcript.WriteNote("login", nil)
			m.transcript.WriteError(err.Error())
		}
		m.refreshViewport()
		return m, nil
	}
	if lowered == "status" {
		m.writeAuthStatus()
		m.refreshViewport()
		return m, nil
	}
	if provider, ok := parseClearAuthProvider(value); ok {
		m.clearProviderSecret(provider)
		m.refreshViewport()
		return m, nil
	}
	if lowered == "openai-codex" {
		return m.startOpenAICodexLoginFlow("", "")
	}
	if strings.HasPrefix(lowered, "openai-codex ") {
		callbackOrCode := strings.TrimSpace(value[len("openai-codex "):])
		if callbackOrCode == "" {
			m.transcript.WriteNote("login", loginUsageLines())
			m.refreshViewport()
			return m, nil
		}
		if m.login.FlowID == "" {
			m.transcript.WriteError("no active ChatGPT login flow")
			m.refreshViewport()
			return m, nil
		}
		return m.submitOpenAICodexLoginCompletion(callbackOrCode)
	}
	m.transcript.WriteNote("login", loginUsageLines())
	m.refreshViewport()
	return m, nil
}

func (m *model) submitOpenAICodexLoginCompletion(callbackOrCode string) (tea.Model, tea.Cmd) {
	if m.login.FlowID == "" {
		m.transcript.WriteError("login flow not ready yet")
		m.refreshViewport()
		return m, nil
	}
	m.textInput.SetValue("")
	m.refreshViewport()
	return m, completeOpenAICodexLogin(m.options.Backend, m.login.FlowID, callbackOrCode)
}

func loginUsageLines() []string {
	return []string{
		"Choose a login lane:",
		"  /login openai-codex       ChatGPT subscription",
		"  /login openai             OpenAI API key",
		"  /login anthropic          Anthropic API key",
		"",
		"Other login commands:",
		"  /login status             show provider readiness",
		"  /login clear <provider>   clear stored auth.json secret",
	}
}

func startOpenAICodexLogin(backend Backend) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		response, err := backend.StartOpenAICodexLogin(ctx)
		return startOpenAICodexLoginMsg{Response: response, Err: err}
	}
}

func completeOpenAICodexLogin(backend Backend, flowID string, callbackOrCode string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		response, err := backend.CompleteOpenAICodexLogin(ctx, flowID, callbackOrCode)
		return completeOpenAICodexLoginMsg{Response: response, Err: err}
	}
}

func waitOpenAICodexLogin(backend Backend, flowID string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authLoginWaitTimeout)
		defer cancel()
		response, err := backend.WaitOpenAICodexLogin(ctx, flowID)
		return waitOpenAICodexLoginMsg{Response: response, Err: err}
	}
}

func bestEffortOpenBrowser(url string) error {
	if strings.TrimSpace(url) == "" {
		return errors.New("missing browser URL")
	}
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", url)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("open browser: %w", err)
	}
	return nil
}
