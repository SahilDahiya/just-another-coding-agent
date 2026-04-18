package app

import tea "github.com/charmbracelet/bubbletea"

func (m *model) approvalTitle() string {
	return "Approval required"
}

func (m *model) approvalReason() string {
	if m.pendingApproval == nil {
		return ""
	}
	return m.pendingApproval.Reason
}

func (m *model) approvalOptionLines() []string {
	return []string{
		"1. Approve",
		"2. Deny",
	}
}

func (m *model) approvalHelpLines() []string {
	return []string{
		"Use up/down to choose an action.",
		"Enter selects. Esc chooses Deny.",
	}
}

func (m *model) handleApprovalKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		return m.completeApprovalSelection(1)
	case "up":
		if m.approval.Selected > 0 {
			m.approval.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down":
		if m.approval.Selected < len(m.approvalOptionLines())-1 {
			m.approval.Selected++
			m.refreshViewport()
		}
		return m, nil
	case "1", "2":
		m.approval.Selected = int(msg.Runes[0] - '1')
		if m.approval.Selected >= len(m.approvalOptionLines()) {
			m.approval.Selected = len(m.approvalOptionLines()) - 1
		}
		m.refreshViewport()
		return m, nil
	case "enter":
		return m.completeApprovalSelection(m.approval.Selected)
	default:
		return m, nil
	}
}

func (m *model) completeApprovalSelection(selection int) (tea.Model, tea.Cmd) {
	decision := "approved"
	if selection != 0 {
		decision = "denied"
	}
	return m.handleApprovalCommand(decision)
}
