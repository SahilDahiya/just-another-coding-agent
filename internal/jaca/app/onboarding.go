package app

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

func (m *model) maybeStartOnboarding() {
	if m.startupOnboardingSet || m.onboarding.Active || m.auth.Active || m.streaming {
		return
	}
	if strings.TrimSpace(m.textInput.Value()) != "" {
		return
	}
	cfg, err := config.Load()
	if err != nil {
		return
	}

	hasPersistedProvider := strings.TrimSpace(cfg["default_provider"]) != ""
	if !hasPersistedProvider {
		m.startupOnboardingSet = true
		m.onboarding = onboardingState{Active: true, Kind: "provider", Selected: 0}
		return
	}

	statuses := m.authStatus
	if statuses == nil {
		return
	}

	selectedProvider := m.currentProvider()
	if selectedProvider == "ollama" {
		hosted, err := m.ollamaUsesHostedEndpoint()
		if err != nil {
			return
		}
		if hosted && !providerConfigured(*statuses, "ollama") {
			m.startupOnboardingSet = true
			if err := m.startCredentialSetup("ollama", "", "", "", ""); err != nil {
				m.transcript.WriteError(err.Error())
			}
		}
		return
	}

	if !providerConfigured(*statuses, selectedProvider) {
		m.startupOnboardingSet = true
		if err := m.startCredentialSetup(selectedProvider, "", "", "", ""); err != nil {
			m.transcript.WriteError(err.Error())
		}
	}
}

func (m *model) shouldShowFirstRunPromptAssist() bool {
	if !m.startupOnboardingSet || m.onboarding.Active || m.auth.Active || m.streaming {
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

func firstRunOptionLines() []string {
	return []string{
		"1. Ollama",
		"2. OpenAI",
		"3. Anthropic",
		"4. Google Gemini",
	}
}

func onboardingSelectionForProvider(provider string) int {
	switch provider {
	case "ollama":
		return 0
	case "openai":
		return 1
	case "anthropic":
		return 2
	case "google":
		return 3
	default:
		return 0
	}
}

func ollamaOnboardingOptionLines() []string {
	return []string{
		"1. Local Ollama",
		"2. Hosted Ollama",
	}
}

func (m *model) onboardingTitle() string {
	switch m.onboarding.Kind {
	case "ollama":
		return "Choose Ollama Mode"
	default:
		return "Get Started"
	}
}

func (m *model) onboardingOptionLines() []string {
	switch m.onboarding.Kind {
	case "ollama":
		return ollamaOnboardingOptionLines()
	default:
		return firstRunOptionLines()
	}
}

func (m *model) onboardingHelpLines() []string {
	switch m.onboarding.Kind {
	case "ollama":
		return []string{
			"Local Ollama uses /model ollama:<local-model> and needs no key",
			"Hosted Ollama uses https://ollama.com/v1 and requires an API key",
			"Enter selects. Esc goes back.",
		}
	default:
		return []string{
			"Choose a provider to get started",
			"Enter selects. Esc closes this panel.",
		}
	}
}

func providerConfigured(statuses rpc.AuthStatusResponse, provider string) bool {
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured
		}
	}
	return false
}

func (m *model) handleOnboardingKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		if m.onboarding.Kind == "ollama" {
			m.onboarding = onboardingState{Active: true, Kind: "provider", Selected: 0}
		} else {
			m.onboarding = onboardingState{}
		}
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
	case "1", "2", "3", "4":
		m.onboarding.Selected = int(msg.Runes[0] - '1')
		if m.onboarding.Selected >= len(m.onboardingOptionLines()) {
			m.onboarding.Selected = len(m.onboardingOptionLines()) - 1
		}
		m.refreshViewport()
		return m, nil
	case "enter":
		return m.completeOnboardingSelection()
	default:
		return m, nil
	}
}

func (m *model) completeOnboardingSelection() (tea.Model, tea.Cmd) {
	selection := m.onboarding.Selected
	kind := m.onboarding.Kind
	switch selection {
	case 0:
		if kind == "ollama" {
			if err := config.SaveOllamaBaseURL(""); err != nil {
				m.transcript.WriteError(err.Error())
				m.refreshViewport()
				return m, nil
			}
			if m.options.Backend != nil {
				m.restartBackendWithCurrentEnv()
			}
			m.onboarding = onboardingState{}
			lines := []string{"Local Ollama selected."}
			switch {
			case m.isHostedOllamaModel(m.options.Model):
				lines = append(
					lines,
					"Current model unchanged.",
					"Pick a local model with /model ollama:<local-model>.",
				)
			case providerForModel(m.options.Model) == "ollama":
				lines = append(
					lines,
					fmt.Sprintf("Current model stays %s.", m.options.Model),
				)
			default:
				lines = append(
					lines,
					"Current model unchanged.",
					"Pick a local model with /model ollama:<local-model>.",
				)
			}
			m.transcript.WriteNote("provider setup", lines)
			m.refreshViewport()
			return m, nil
		}
		m.onboarding = onboardingState{Active: true, Kind: "ollama", Selected: 0}
	case 1:
		if kind == "ollama" {
			hasCreds, err := m.ollamaCloudConfigured()
			if err != nil {
				m.onboarding = onboardingState{}
				m.transcript.WriteError(err.Error())
				m.refreshViewport()
				return m, nil
			}
			if !hasCreds {
				m.onboarding = onboardingState{}
				if err := m.startCredentialSetup("ollama", "ollama", "", "provider", ""); err != nil {
					m.transcript.WriteError(err.Error())
				}
				m.refreshViewport()
				return m, nil
			}
			m.onboarding = onboardingState{}
			lines, restart, err := m.applyProviderSelection("ollama")
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
		return m.completeAuthenticatedOnboardingProviderSelection("openai")
	case 2:
		return m.completeAuthenticatedOnboardingProviderSelection("anthropic")
	case 3:
		return m.completeAuthenticatedOnboardingProviderSelection("google")
	}
	m.refreshViewport()
	return m, nil
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
