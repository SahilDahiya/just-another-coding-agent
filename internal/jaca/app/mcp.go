package app

import (
	"fmt"

	"jaca/internal/jaca/rpc"
)

func (m *model) writeMcpStatus() {
	m.transcript.WriteNote("mcp", nil)
	statuses, err := m.fetchAuthStatus()
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	m.authStatus = &statuses
	for _, line := range mcpStatusLines(statuses.McpServers) {
		m.transcript.WriteLine(line)
	}
}

func mcpStatusLines(statuses []rpc.McpServerAuthStatus) []string {
	if len(statuses) == 0 {
		return []string{"no MCP servers configured"}
	}
	lines := make([]string, 0, len(statuses))
	for _, status := range statuses {
		line := fmt.Sprintf(
			"%s: transport=%s auth=%s configured=%t reason=%s",
			status.ServerID,
			status.TransportType,
			status.AuthKind,
			status.Configured,
			status.Reason,
		)
		if status.EnvVar != nil && *status.EnvVar != "" {
			line = fmt.Sprintf("%s env=%s", line, *status.EnvVar)
		}
		lines = append(lines, line)
	}
	return lines
}
