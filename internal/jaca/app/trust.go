package app

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
)

func (m *model) trustTitle() string {
	return "Trust This Directory?"
}

func (m *model) trustBodyLines() []string {
	target := m.trust.TrustTarget
	if target == "" {
		target = m.options.WorkspaceRoot
	}
	return []string{
		fmt.Sprintf("You are in %s", target),
		"Working with untrusted contents comes with higher risk of prompt injection.",
	}
}

func (m *model) trustOptionLines() []string {
	return []string{
		"1. Yes, continue",
		"2. No, quit",
	}
}

func (m *model) trustHelpLines() []string {
	return []string{
		"Trust is remembered for this project root.",
		"Use /trust revoke later if you want to clear it.",
		"Repo instructions and session bootstrap stay blocked until trusted.",
		"Enter selects. Esc quits.",
	}
}

func (m *model) handleTrustKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		return m, tea.Quit
	case "up":
		if m.trust.Selected > 0 {
			m.trust.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down":
		if m.trust.Selected < len(m.trustOptionLines())-1 {
			m.trust.Selected++
			m.refreshViewport()
		}
		return m, nil
	case "1", "2":
		m.trust.Selected = int(msg.Runes[0] - '1')
		m.refreshViewport()
		return m, nil
	case "enter":
		if m.trust.Selected == 0 {
			return m, acceptWorkspaceTrust(m.options.Backend)
		}
		return m, tea.Quit
	default:
		return m, nil
	}
}
