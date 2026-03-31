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
	statuses := m.authStatus
	if statuses == nil {
		return
	}

	cfg, err := config.Load()
	if err != nil {
		return
	}

	selectedProvider := m.currentProvider()
	hasPersistedProvider := strings.TrimSpace(cfg["default_provider"]) != ""
	if !hasPersistedProvider {
		m.startupOnboardingSet = true
		m.onboarding = onboardingState{Active: true, Kind: "provider", Selected: 0}
		return
	}

	if selectedProvider == "ollama" {
		if m.modelCatalog != nil && m.ollamaCloudSelectionRequiresAuth() && !providerConfigured(*statuses, "ollama") {
			m.startupOnboardingSet = true
			m.transcript.WriteNote(
				"provider setup",
				[]string{
					"the shipped Ollama provider path uses hosted Ollama models",
					"paste your Ollama cloud API key now, or press esc to cancel",
					"for local Ollama instead, cancel and run /model ollama:<installed-model>",
				},
			)
			m.startAuthFlow("ollama", "", "")
		}
		return
	}

	if !providerConfigured(*statuses, selectedProvider) {
		m.startupOnboardingSet = true
		m.transcript.WriteNote(
			"provider setup",
			[]string{
				fmt.Sprintf("%s is selected but not configured yet.", selectedProvider),
				"enter the provider secret now, or press esc to cancel",
				"local secrets are stored in the OS keychain",
			},
		)
		m.startAuthFlow(selectedProvider, "", "")
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
		"2. GitHub Models",
		"3. OpenAI",
		"4. Anthropic",
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
			"Hosted Ollama uses /provider ollama and may require auth",
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
			m.onboarding = onboardingState{}
			m.transcript.WriteNote("provider setup", []string{
				"Use /model ollama:<local-model> for local no-auth use.",
				"Example: /model ollama:llama3.2",
			})
			m.refreshViewport()
			return m, nil
		}
		m.onboarding = onboardingState{Active: true, Kind: "ollama", Selected: 0}
	case 1:
		if kind == "ollama" {
			m.onboarding = onboardingState{}
			m.startAuthFlow("ollama", "ollama", "")
			m.refreshViewport()
			return m, nil
		}
		m.onboarding = onboardingState{}
		m.startAuthFlow("github", "github", "")
	case 2:
		m.onboarding = onboardingState{}
		m.startAuthFlow("openai", "openai", "")
	case 3:
		m.onboarding = onboardingState{}
		m.startAuthFlow("anthropic", "anthropic", "")
	}
	m.refreshViewport()
	return m, nil
}

func (m *model) ollamaCloudSelectionRequiresAuth() bool {
	if m.modelCatalog == nil {
		return false
	}
	for _, provider := range m.modelCatalog.Providers {
		if provider.Provider != "ollama" {
			continue
		}
		for _, model := range provider.Models {
			if model.ModelID == m.options.Model {
				return true
			}
		}
		return false
	}
	return false
}
