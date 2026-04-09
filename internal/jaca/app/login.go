package app

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"runtime"
	"strings"
	"time"

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

type pollOpenAICodexLoginMsg struct {
	Response rpc.AuthLoginOpenAICodexPollResponse
	Err      error
}

type startGitHubCopilotLoginMsg struct {
	Response rpc.AuthLoginGitHubCopilotStartResponse
	Err      error
}

type pollGitHubCopilotLoginMsg struct {
	Response rpc.AuthLoginGitHubCopilotPollResponse
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

func (m *model) startGitHubCopilotLoginFlow(
	pendingModel string,
	pendingPrompt string,
) (tea.Model, tea.Cmd) {
	if m.options.Backend == nil {
		m.transcript.WriteError("backend unavailable")
		m.refreshViewport()
		return m, nil
	}
	m.login = loginState{
		Provider:      "github-copilot",
		PendingModel:  pendingModel,
		PendingPrompt: pendingPrompt,
	}
	m.textInput.EchoMode = textinput.EchoNormal
	m.textInput.SetValue("")
	m.textInput.CursorEnd()
	m.clearSlashMenu()
	m.promptFooterNotice = ""
	m.refreshViewport()
	return m, startGitHubCopilotLogin(m.options.Backend, "")
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
	if m.login.FlowID == "" {
		m.transcript.WriteError("login flow not ready yet")
		m.refreshViewport()
		return m, nil
	}
	if m.login.Provider != "openai-codex" {
		m.transcript.WriteNote("login", []string{
			"GitHub Copilot device-code login completes in the browser.",
			"Wait for approval or restart with /login github-copilot.",
		})
		m.refreshViewport()
		return m, nil
	}
	m.textInput.SetValue("")
	m.refreshViewport()
	return m, completeOpenAICodexLogin(m.options.Backend, m.login.FlowID, value)
}

func (m *model) handleLoginCommand(arg string) (tea.Model, tea.Cmd) {
	value := strings.TrimSpace(arg)
	lowered := strings.ToLower(value)
	if lowered == "" {
		m.transcript.WriteNote("login", []string{
			"Choose a login lane:",
			"  /login openai-codex       ChatGPT subscription",
			"  /login github-copilot     GitHub Copilot subscription",
		})
		m.refreshViewport()
		return m, nil
	}
	if lowered == "openai-codex" {
		return m.startOpenAICodexLoginFlow("", "")
	}
	if lowered == "github-copilot" {
		return m.startGitHubCopilotLoginFlow("", "")
	}
	if strings.HasPrefix(lowered, "openai-codex ") {
		callbackOrCode := strings.TrimSpace(value[len("openai-codex "):])
		if callbackOrCode == "" {
			m.transcript.WriteNote("login", nil)
			m.transcript.WriteLine(
				"usage: /login openai-codex [redirect-url-or-code]",
			)
			m.refreshViewport()
			return m, nil
		}
		if m.login.FlowID == "" {
			m.transcript.WriteError("no active ChatGPT login flow")
			m.refreshViewport()
			return m, nil
		}
		return m, completeOpenAICodexLogin(
			m.options.Backend,
			m.login.FlowID,
			callbackOrCode,
		)
	}
	m.transcript.WriteNote("login", nil)
	m.transcript.WriteError(
		"usage: /login openai-codex [redirect-url-or-code] | /login github-copilot",
	)
	m.refreshViewport()
	return m, nil
}

func openAICodexLoginSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "openai-codex", Description: "ChatGPT subscription"},
		{Value: "github-copilot", Description: "GitHub Copilot subscription"},
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

func pollOpenAICodexLogin(backend Backend, flowID string) tea.Cmd {
	return tea.Tick(350*time.Millisecond, func(time.Time) tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		response, err := backend.PollOpenAICodexLogin(ctx, flowID)
		return pollOpenAICodexLoginMsg{Response: response, Err: err}
	})
}

func startGitHubCopilotLogin(backend Backend, enterpriseDomain string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		response, err := backend.StartGitHubCopilotLogin(ctx, enterpriseDomain)
		return startGitHubCopilotLoginMsg{Response: response, Err: err}
	}
}

func pollGitHubCopilotLogin(backend Backend, flowID string) tea.Cmd {
	return tea.Tick(1200*time.Millisecond, func(time.Time) tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		response, err := backend.PollGitHubCopilotLogin(ctx, flowID)
		return pollGitHubCopilotLoginMsg{Response: response, Err: err}
	})
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
