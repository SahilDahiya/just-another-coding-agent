package app

import (
	"fmt"
	"strings"
)

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
		"1. Approve and continue",
		"2. Deny request",
	}
}

func (m *model) approvalDetailLines() []string {
	if m.pendingApproval == nil {
		return nil
	}
	lines := []string{
		fmt.Sprintf(
			"requested posture: fs=%s, net=%s, exec=%s",
			m.pendingApproval.RequestedCapabilities.FilesystemAccess,
			m.pendingApproval.RequestedCapabilities.NetworkAccess,
			m.pendingApproval.RequestedCapabilities.ExecutionIsolation,
		),
	}
	if m.pendingApproval.RequestedPermissions == nil {
		return lines
	}
	permissions := m.pendingApproval.RequestedPermissions
	if permissions.NetworkAccess != nil {
		lines = append(lines, fmt.Sprintf("network: %s", *permissions.NetworkAccess))
	}
	if len(permissions.ExtraReadRoots) > 0 {
		lines = append(
			lines,
			fmt.Sprintf("read roots: %s", joinForDisplay(permissions.ExtraReadRoots)),
		)
	}
	if len(permissions.ExtraWriteRoots) > 0 {
		lines = append(
			lines,
			fmt.Sprintf("write roots: %s", joinForDisplay(permissions.ExtraWriteRoots)),
		)
	}
	return lines
}

func (m *model) approvalHelpLines() []string {
	return []string{
		"Select an action for this request.",
		"Enter confirms the selected action. Esc denies immediately.",
	}
}

func joinForDisplay(values []string) string {
	return strings.Join(values, ", ")
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
