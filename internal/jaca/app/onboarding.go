package app

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

func (m *model) maybeStartOnboarding() tea.Cmd {
	if m.startupOnboardingSet || m.onboarding.Active || m.auth.Active || m.streaming {
		return nil
	}
	if m.workspaceTrust == nil || !m.workspaceTrust.Trusted {
		return nil
	}
	if strings.TrimSpace(m.textInput.Value()) != "" {
		return nil
	}
	cfg, err := config.Load()
	if err != nil {
		return nil
	}

	hasPersistedProvider := strings.TrimSpace(cfg["default_provider"]) != ""
	if !hasPersistedProvider {
		m.startupOnboardingSet = true
		m.onboarding = onboardingState{Active: true, Kind: "provider", Selected: 0}
		return nil
	}

	statuses := m.authStatus
	if statuses == nil {
		return nil
	}
	if !hasAvailableLoginLane(*statuses) {
		m.startupOnboardingSet = true
		m.onboarding = onboardingState{Active: true, Kind: "provider", Selected: 0}
		return nil
	}

	selectedModel := strings.TrimSpace(cfg["default_model"])
	if selectedModel == "" {
		selectedModel = m.options.Model
	}
	if isOpenAICodexOAuthModel(selectedModel) {
		if !oauthProviderLoggedIn(*statuses, "openai-codex") {
			m.startupOnboardingSet = true
			_, cmd := m.startOpenAICodexLoginFlow("", "")
			return cmd
		}
		return nil
	}

	selectedProvider := m.currentProvider()
	if !providerConfigured(*statuses, selectedProvider) {
		m.startupOnboardingSet = true
		if err := m.startCredentialSetup(selectedProvider, "", "", "", ""); err != nil {
			m.transcript.WriteError(err.Error())
		}
	}
	return nil
}

func (m *model) shouldShowFirstRunPromptAssist() bool {
	if !m.startupOnboardingSet || m.onboarding.Active || m.auth.Active || m.streaming {
		return false
	}
	if m.workspaceTrust == nil || !m.workspaceTrust.Trusted {
		return false
	}
	if strings.TrimSpace(m.textInput.Value()) != "" {
		return false
	}
	cfg, err := config.Load()
	if err != nil {
		return false
	}
	return strings.TrimSpace(cfg["default_provider"]) == ""
}

func onboardingSelectionForProvider(provider string) int {
	switch provider {
	case "openai":
		return 1
	case "anthropic":
		return 2
	default:
		return 0
	}
}

func (m *model) onboardingTitle() string {
	if m.onboarding.Kind == "mcq" {
		return "Codebase Onboarding"
	}
	return "Connect JACA"
}

func (m *model) onboardingBodyLines() []string {
	if m.onboarding.Kind != "mcq" {
		return nil
	}
	return []string{m.onboarding.Prompt}
}

func (m *model) onboardingTranscriptLines() []string {
	if m.onboarding.Kind != "mcq" {
		return nil
	}
	return []string{m.onboarding.Prompt}
}

func isOnboardingSubmitKey(msg tea.KeyMsg) bool {
	switch msg.String() {
	case "enter", "ctrl+j", "ctrl+m":
		return true
	default:
		return false
	}
}

func (m *model) onboardingOptionLines() []string {
	if m.onboarding.Kind == "mcq" {
		rows := make([]string, 0, len(m.onboarding.Options))
		for index, option := range m.onboarding.Options {
			rows = append(rows, fmt.Sprintf("%d. %s", index+1, option))
		}
		return rows
	}
	return []string{
		onboardingOptionLine(
			"1. ChatGPT subscription",
			"browser login",
			m.onboardingOAuthStatus("openai-codex"),
		),
		onboardingOptionLine(
			"2. OpenAI API key",
			"API key",
			m.onboardingProviderStatus("openai"),
		),
		onboardingOptionLine(
			"3. Anthropic API key",
			"API key",
			m.onboardingProviderStatus("anthropic"),
		),
	}
}

func (m *model) onboardingHelpLines() []string {
	if m.onboarding.Kind == "mcq" {
		return []string{
			"Use up/down to choose one answer.",
			"Press Enter to submit.",
		}
	}
	return []string{
		"Each lane shows setup and readiness.",
		"API-key setup prepares ~/.jaca/auth.json for you.",
		"Enter selects. Esc closes.",
	}
}

func hasAvailableLoginLane(statuses rpc.AuthStatusResponse) bool {
	if oauthProviderLoggedIn(statuses, "openai-codex") {
		return true
	}
	return providerConfigured(statuses, "openai") || providerConfigured(statuses, "anthropic")
}

func onboardingOptionLine(title string, setup string, status string) string {
	details := make([]string, 0, 2)
	if strings.TrimSpace(setup) != "" {
		details = append(details, setup)
	}
	if strings.TrimSpace(status) != "" {
		details = append(details, status)
	}
	if len(details) == 0 {
		return title
	}
	return title + "\n   " + strings.Join(details, " · ")
}

func (m *model) onboardingProviderStatus(provider string) string {
	if m.authStatus == nil {
		return "status loading"
	}
	for _, status := range m.authStatus.Providers {
		if status.Provider != provider {
			continue
		}
		if !status.Configured {
			return "needs API key"
		}
		switch status.Source {
		case "env":
			return "ready from env"
		case "file":
			return "ready from auth file"
		default:
			return "ready"
		}
	}
	return "status loading"
}

func (m *model) onboardingOAuthStatus(provider string) string {
	if m.authStatus == nil {
		return "status loading"
	}
	if oauthProviderLoggedIn(*m.authStatus, provider) {
		return "logged in"
	}
	return "needs login"
}

func providerConfigured(statuses rpc.AuthStatusResponse, provider string) bool {
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured
		}
	}
	return false
}

func oauthProviderLoggedIn(statuses rpc.AuthStatusResponse, provider string) bool {
	for _, status := range statuses.OAuthProviders {
		if status.Provider == provider {
			return status.LoggedIn
		}
	}
	return false
}

func (m *model) handleOnboardingKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.onboarding.Kind == "mcq" {
		return m.handleOnboardingMcqKey(msg)
	}
	switch msg.String() {
	case "esc":
		m.onboarding = onboardingState{}
		m.refreshViewport()
		return m, nil
	case "up":
		if m.onboarding.Selected > 0 {
			m.onboarding.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down":
		if m.onboarding.Selected < len(m.onboardingOptionLines())-1 {
			m.onboarding.Selected++
			m.refreshViewport()
		}
		return m, nil
	case "1", "2", "3":
		m.onboarding.Selected = int(msg.Runes[0] - '1')
		if m.onboarding.Selected >= len(m.onboardingOptionLines()) {
			m.onboarding.Selected = len(m.onboardingOptionLines()) - 1
		}
		m.refreshViewport()
		return m, nil
	default:
		if isOnboardingSubmitKey(msg) {
			return m.completeOnboardingSelection()
		}
		return m, nil
	}
}

func (m *model) handleOnboardingMcqKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		if m.streaming {
			return m, nil
		}
		m.onboarding.Active = false
		m.refreshViewport()
		return m, nil
	case "up":
		if m.onboarding.Selected > 0 {
			m.onboarding.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down":
		if m.onboarding.Selected < len(m.onboarding.Options)-1 {
			m.onboarding.Selected++
			m.refreshViewport()
		}
		return m, nil
	case "1", "2", "3", "4":
		m.onboarding.Selected = int(msg.Runes[0] - '1')
		if m.onboarding.Selected >= len(m.onboarding.Options) {
			m.onboarding.Selected = len(m.onboarding.Options) - 1
		}
		m.refreshViewport()
		return m, nil
	default:
		if isOnboardingSubmitKey(msg) {
			return m.completeOnboardingSelection()
		}
		return m, nil
	}
}

func (m *model) completeOnboardingSelection() (tea.Model, tea.Cmd) {
	if m.onboarding.Kind == "mcq" {
		return m, submitOnboardingSelection(
			m.options.Backend,
			m.sessionID,
			m.onboarding.AttemptID,
			m.onboarding.Selected,
		)
	}
	selection := m.onboarding.Selected
	m.onboarding = onboardingState{}
	switch selection {
	case 0:
		return m.startOpenAICodexLoginFlow("", "")
	case 1:
		return m.completeAuthenticatedOnboardingProviderSelection("openai")
	case 2:
		return m.completeAuthenticatedOnboardingProviderSelection("anthropic")
	}
	m.refreshViewport()
	return m, nil
}

func (m *model) executeOnboardSlash(args string) (tea.Model, tea.Cmd) {
	if m.workspaceTrustLoading || m.workspaceTrust == nil {
		m.promptFooterNotice = "checking workspace trust"
		m.refreshViewport()
		return m, nil
	}
	if !m.workspaceTrust.Trusted {
		m.trust.Active = true
		m.refreshViewport()
		return m, nil
	}
	prompt := strings.TrimSpace(args)
	if prompt == "" {
		prompt = "Onboard me to this repository."
	}
	return m.submitPromptWithMode(prompt, prompt, "onboarding")
}

func (m *model) executeExitModeSlash(_ string) (tea.Model, tea.Cmd) {
	if strings.TrimSpace(m.sessionID) == "" {
		m.transcript.WriteError("No active session to exit mode for.")
		m.refreshViewport()
		return m, nil
	}
	m.refreshViewport()
	return m, setSessionMode(m.options.Backend, m.sessionID, "coding")
}

func (m *model) completeAuthenticatedOnboardingProviderSelection(provider string) (tea.Model, tea.Cmd) {
	hasCreds, err := m.providerHasCredentials(provider)
	if err != nil {
		m.onboarding = onboardingState{}
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}
	if !hasCreds {
		m.onboarding = onboardingState{}
		if err := m.startCredentialSetup(provider, provider, "", "provider", ""); err != nil {
			m.transcript.WriteError(err.Error())
		}
		m.refreshViewport()
		return m, nil
	}

	m.onboarding = onboardingState{}
	lines, restart, err := m.applyProviderSelection(provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}
	for _, line := range lines {
		m.transcript.WriteLine(line)
	}
	if restart && m.options.Backend != nil {
		m.restartBackendWithCurrentEnv()
	}
	m.refreshViewport()
	return m, nil
}

func submitOnboardingSelection(
	backend Backend,
	sessionID string,
	attemptID string,
	selectedIndex int,
) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), queueControlTimeout)
		defer cancel()
		response, err := backend.SubmitOnboarding(
			ctx,
			sessionID,
			attemptID,
			selectedIndex,
		)
		return onboardingSubmittedMsg{Response: response, Err: err}
	}
}
