package app

import (
	"context"
	"fmt"
	"strings"
)

func (m *model) writeSessionInfo() {
	m.transcript.WriteNote("session", nil)
	if m.sessionID == "" {
		m.transcript.WriteLine("no active session")
		return
	}
	if m.sessionName != "" {
		m.transcript.WriteLine(fmt.Sprintf("session: %s", m.sessionName))
		m.transcript.WriteLine(fmt.Sprintf("id: %s", m.sessionID))
		return
	}
	m.transcript.WriteLine(fmt.Sprintf("session: %s", m.sessionID))
}

func (m *model) handleSessionNameCommand(raw string) {
	m.transcript.WriteNote("session", nil)
	name := strings.TrimSpace(raw)
	if name == "" {
		m.transcript.WriteError("usage: /name <session-name>")
		return
	}
	if m.sessionID == "" {
		m.transcript.WriteError("no active session")
		return
	}
	if m.options.Backend == nil {
		m.transcript.WriteError("session naming backend unavailable")
		return
	}
	response, err := m.options.Backend.SetSessionName(context.Background(), m.sessionID, name)
	if err != nil {
		m.transcript.WriteError(err.Error())
		return
	}
	m.sessionName = response.Name
	m.transcript.WriteLine(fmt.Sprintf("session named %s", response.Name))
}
