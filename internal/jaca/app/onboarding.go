package app

import (
	"fmt"
	"strings"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

func (m *model) maybeStartOnboarding() {
	if m.startupOnboardingSet || m.auth.Active || m.streaming {
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
		m.transcript.WriteNote("first-time setup", m.firstRunOnboardingLines(*statuses))
		return
	}

	if selectedProvider == "ollama" {
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

func (m *model) firstRunOnboardingLines(statuses rpc.AuthStatusResponse) []string {
	lines := []string{
		"choose a provider to get started:",
		"  1. /provider ollama      local or configured Ollama endpoint, no key for local",
		"  2. /provider github      GitHub Models, auth starts if needed",
		"  3. /provider openai      OpenAI, auth starts if needed",
		"  4. /provider anthropic   Anthropic, auth starts if needed",
		"",
		"local secrets are stored in the OS keychain",
		"env vars override keychain values for headless and CI runs",
		"run /auth status any time to inspect provider auth state",
	}
	configured := configuredProviderLines(statuses)
	if len(configured) > 0 {
		lines = append(lines, "")
		lines = append(lines, "already configured now:")
		lines = append(lines, configured...)
	}
	return lines
}

func configuredProviderLines(statuses rpc.AuthStatusResponse) []string {
	lines := []string{}
	for _, status := range statuses.Providers {
		if !status.Configured {
			continue
		}
		lines = append(
			lines,
			fmt.Sprintf("  - %s (%s)", status.Provider, status.Source),
		)
	}
	return lines
}

func providerConfigured(statuses rpc.AuthStatusResponse, provider string) bool {
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured
		}
	}
	return false
}
