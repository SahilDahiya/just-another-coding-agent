package app

import (
	"fmt"
)

import tea "github.com/charmbracelet/bubbletea"

import "jaca/internal/jaca/rpc"

func (m *model) approvalTitle() string {
	return "Approval required"
}

func (m *model) approvalReason() string {
	if m.pendingApproval == nil {
		return ""
	}
	if m.pendingApproval.DisplaySubject != "" {
		return m.pendingApproval.DisplaySubject
	}
	switch m.pendingApproval.RequestKind {
	case "command_execution":
		return m.pendingApproval.Command
	case "file_change":
		return fmt.Sprintf("%s %s", m.pendingApproval.ChangeKind, m.pendingApproval.Path)
	case "permission_grant":
		if m.pendingApproval.Target != "" {
			return fmt.Sprintf("%s %s", approvalGrantVerb(m.pendingApproval.GrantKind), m.pendingApproval.Target)
		}
	}
	return m.pendingApproval.Reason
}

func (m *model) approvalOptionLines() []string {
	if m.pendingApproval != nil && len(m.pendingApproval.Options) > 0 {
		lines := make([]string, 0, len(m.pendingApproval.Options))
		for index, option := range m.pendingApproval.Options {
			lines = append(lines, fmt.Sprintf("%d. %s", index+1, option.Label))
		}
		return lines
	}
	return []string{
		"1. Allow once",
		"2. Deny",
	}
}

func (m *model) approvalDetailLines() []string {
	return nil
}

func (m *model) approvalHelpLines() []string {
	return nil
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
	decision, ok := m.approvalDecisionForSelection(selection)
	if !ok {
		m.transcript.WriteError("invalid approval selection")
		m.refreshViewport()
		return m, nil
	}
	if m.options.Backend == nil {
		m.transcript.WriteError("backend unavailable")
		m.refreshViewport()
		return m, nil
	}
	if m.sessionID == "" {
		m.transcript.WriteError("no active session")
		m.refreshViewport()
		return m, nil
	}
	return m, submitApprovalDecision(
		m.options.Backend,
		m.sessionID,
		decision,
	)
}

func (m *model) approvalDecisionForSelection(selection int) (rpc.ApprovalDecision, bool) {
	if m.pendingApproval == nil {
		return rpc.ApprovalDecision{}, false
	}
	if len(m.pendingApproval.Options) == 0 {
		decision := "approved"
		if selection != 0 {
			decision = "denied"
		}
		return rpc.ApprovalDecision{
			RequestID: m.pendingApproval.RequestID,
			Decision:  decision,
		}, true
	}
	if selection < 0 || selection >= len(m.pendingApproval.Options) {
		return rpc.ApprovalDecision{}, false
	}
	option := m.pendingApproval.Options[selection]
	return rpc.ApprovalDecision{
		RequestID: m.pendingApproval.RequestID,
		Decision:  option.Decision,
		OptionID:  option.OptionID,
	}, true
}

func (m *model) approvalDecisionForIntent(decision string) (rpc.ApprovalDecision, bool) {
	if m.pendingApproval == nil {
		return rpc.ApprovalDecision{}, false
	}
	if len(m.pendingApproval.Options) == 0 {
		return rpc.ApprovalDecision{
			RequestID: m.pendingApproval.RequestID,
			Decision:  decision,
		}, true
	}
	for _, option := range m.pendingApproval.Options {
		if option.Decision != decision {
			continue
		}
		return rpc.ApprovalDecision{
			RequestID: m.pendingApproval.RequestID,
			Decision:  option.Decision,
			OptionID:  option.OptionID,
		}, true
	}
	return rpc.ApprovalDecision{}, false
}

func approvalGrantVerb(grantKind string) string {
	switch grantKind {
	case "filesystem_read":
		return "read"
	case "filesystem_write":
		return "write"
	default:
		return grantKind
	}
}
