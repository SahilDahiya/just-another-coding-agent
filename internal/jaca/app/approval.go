package app

import (
	"fmt"
	"strings"
)

import tea "github.com/charmbracelet/bubbletea"

func (m *model) approvalTitle() string {
	if m.pendingApproval == nil {
		return "Approval required"
	}
	switch m.pendingApproval.RequestKind {
	case "command_execution":
		return "Command approval required"
	case "file_change":
		return "File change approval required"
	case "permission_grant":
		return "Permission grant required"
	default:
		return "Approval required"
	}
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
		fmt.Sprintf("request kind: %s", approvalRequestKindLabel(m.pendingApproval.RequestKind)),
		fmt.Sprintf(
			"requested posture: fs=%s, net=%s, exec=%s",
			m.pendingApproval.RequestedCapabilities.FilesystemAccess,
			m.pendingApproval.RequestedCapabilities.NetworkAccess,
			m.pendingApproval.RequestedCapabilities.ExecutionIsolation,
		),
	}
	switch m.pendingApproval.RequestKind {
	case "command_execution":
		lines = append(lines, fmt.Sprintf("command: %s", m.pendingApproval.Command))
		if m.pendingApproval.Cwd != "" {
			lines = append(lines, fmt.Sprintf("cwd: %s", m.pendingApproval.Cwd))
		}
		if m.pendingApproval.ShellFamily != "" {
			lines = append(lines, fmt.Sprintf("shell: %s", m.pendingApproval.ShellFamily))
		}
	case "file_change":
		if m.pendingApproval.Path != "" {
			lines = append(lines, fmt.Sprintf("path: %s", m.pendingApproval.Path))
		}
		if m.pendingApproval.ChangeKind != "" {
			lines = append(lines, fmt.Sprintf("change: %s", m.pendingApproval.ChangeKind))
		}
	case "permission_grant":
		if m.pendingApproval.GrantKind != "" {
			lines = append(lines, fmt.Sprintf("grant: %s", m.pendingApproval.GrantKind))
		}
		if m.pendingApproval.Target != "" {
			lines = append(lines, fmt.Sprintf("target: %s", m.pendingApproval.Target))
		}
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

func approvalRequestKindLabel(kind string) string {
	switch kind {
	case "command_execution":
		return "command execution"
	case "file_change":
		return "file change"
	case "permission_grant":
		return "permission grant"
	default:
		return kind
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
