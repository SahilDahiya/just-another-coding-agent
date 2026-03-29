package app

import (
	"fmt"
	"strings"

	"jaca/internal/jaca/config"
)

const maxSlashMenuRows = 6

type slashMenuMode string

const (
	slashMenuHidden    slashMenuMode = ""
	slashMenuCommands  slashMenuMode = "commands"
	slashMenuArguments slashMenuMode = "arguments"
)

type slashSuggestion struct {
	Value       string
	Description string
	AcceptsArgs bool
}

type slashMenuState struct {
	Mode     slashMenuMode
	Command  string
	Rows     []slashSuggestion
	Selected int
}

func (m *model) syncSlashMenu() {
	if m.streaming {
		m.clearSlashMenu()
		return
	}
	state := buildSlashMenuState(m.textInput.Value(), m.currentProvider())
	if state.Mode == slashMenuHidden {
		m.clearSlashMenu()
		return
	}
	if state.Command == m.slashMenu.Command && state.Mode == m.slashMenu.Mode {
		previous := m.slashMenu.Selected
		if previous >= 0 && previous < len(state.Rows) {
			previousValue := m.slashMenu.Rows[previous].Value
			for idx, row := range state.Rows {
				if row.Value == previousValue {
					state.Selected = idx
					break
				}
			}
		}
	}
	if state.Selected >= len(state.Rows) {
		state.Selected = len(state.Rows) - 1
	}
	if state.Selected < 0 {
		state.Selected = 0
	}
	m.slashMenu = state
}

func (m *model) clearSlashMenu() {
	m.slashMenu = slashMenuState{}
}

func (m *model) slashMenuVisible() bool {
	return m.slashMenu.Mode != slashMenuHidden && len(m.slashMenu.Rows) > 0
}

func (m *model) moveSlashSelection(delta int) {
	if !m.slashMenuVisible() {
		return
	}
	next := m.slashMenu.Selected + delta
	if next < 0 {
		next = 0
	}
	if next >= len(m.slashMenu.Rows) {
		next = len(m.slashMenu.Rows) - 1
	}
	m.slashMenu.Selected = next
}

func (m *model) commitSlashSuggestion() {
	if !m.slashMenuVisible() {
		return
	}
	active := m.slashMenu.Rows[m.slashMenu.Selected]
	switch m.slashMenu.Mode {
	case slashMenuCommands:
		if active.AcceptsArgs {
			m.textInput.SetValue(active.Value + " ")
			m.textInput.CursorEnd()
			m.syncSlashMenu()
			return
		}
		m.textInput.SetValue(active.Value)
		m.textInput.CursorEnd()
		m.syncSlashMenu()
	case slashMenuArguments:
		m.textInput.SetValue(fmt.Sprintf("%s %s", m.slashMenu.Command, active.Value))
		m.textInput.CursorEnd()
		m.syncSlashMenu()
	}
}

func (m *model) currentProvider() string {
	if cfg, err := config.Load(); err == nil {
		switch strings.ToLower(cfg["default_provider"]) {
		case "openai", "anthropic", "ollama":
			return strings.ToLower(cfg["default_provider"])
		}
	}
	switch {
	case strings.HasPrefix(strings.ToLower(m.options.Model), "openai:"):
		return "openai"
	case strings.HasPrefix(strings.ToLower(m.options.Model), "anthropic:"):
		return "anthropic"
	default:
		return "ollama"
	}
}

func buildSlashMenuState(input string, provider string) slashMenuState {
	if input == "" || !strings.HasPrefix(input, "/") {
		return slashMenuState{}
	}
	hasTrailingSpace := strings.HasSuffix(input, " ")
	parts := strings.Fields(input)
	if len(parts) == 0 {
		return slashMenuState{}
	}

	commandToken := strings.ToLower(parts[0])
	if len(parts) == 1 && !hasTrailingSpace {
		rows := filterSuggestions(slashCommandSuggestions(), input)
		if len(rows) == 1 && rows[0].Value == commandToken && !rows[0].AcceptsArgs {
			return slashMenuState{}
		}
		return slashMenuState{
			Mode:     slashMenuCommands,
			Command:  commandToken,
			Rows:     rows,
			Selected: 0,
		}
	}

	rawArg := strings.TrimSpace(input[len(parts[0]):])
	var rows []slashSuggestion
	switch commandToken {
	case "/provider":
		rows = filterSuggestions(providerSuggestions(), rawArg)
	case "/model":
		rows = filterSuggestions(modelSuggestions(provider), rawArg)
	case "/trace":
		rows = filterSuggestions(traceSuggestions(), rawArg)
	default:
		return slashMenuState{}
	}
	if len(rows) == 1 && strings.EqualFold(rawArg, rows[0].Value) && !hasTrailingSpace {
		return slashMenuState{}
	}
	return slashMenuState{
		Mode:     slashMenuArguments,
		Command:  commandToken,
		Rows:     rows,
		Selected: 0,
	}
}

func slashCommandSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "/provider", Description: "Switch active provider", AcceptsArgs: true},
		{Value: "/model", Description: "Switch active model", AcceptsArgs: true},
		{Value: "/trace", Description: "Set tracing mode", AcceptsArgs: true},
		{Value: "/thinking", Description: "Set thinking effort", AcceptsArgs: true},
		{Value: "/workspace", Description: "Show current workspace"},
		{Value: "/session", Description: "Show active session"},
		{Value: "/compact", Description: "Compact current session"},
		{Value: "/new", Description: "Clear active session"},
		{Value: "/help", Description: "Show available commands"},
		{Value: "/quit", Description: "Quit JACA"},
	}
}

func providerSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "ollama", Description: "Local or configured Ollama endpoint"},
		{Value: "openai", Description: "OpenAI hosted models"},
		{Value: "anthropic", Description: "Anthropic Claude models"},
	}
}

func traceSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "off", Description: "Disable tracing"},
		{Value: "local", Description: "Store traces locally"},
		{Value: "logfire", Description: "Send traces to Logfire"},
	}
}

func modelSuggestions(provider string) []slashSuggestion {
	switch provider {
	case "openai":
		return []slashSuggestion{
			{Value: "openai:gpt-5.4", Description: "Default GPT-5.4 path"},
			{Value: "openai:gpt-5.4-mini", Description: "Faster GPT-5.4 mini"},
			{Value: "openai:gpt-5.3-codex", Description: "Codex-optimized GPT-5.3"},
		}
	case "anthropic":
		return []slashSuggestion{
			{Value: "anthropic:claude-sonnet-4-5", Description: "Balanced Claude Sonnet"},
			{Value: "anthropic:claude-opus-4-1", Description: "Stronger Claude Opus"},
		}
	default:
		return []slashSuggestion{
			{Value: "ollama:kimi-k2:1t-cloud", Description: "Current default Kimi K2"},
			{Value: "ollama:glm-5:cloud", Description: "GLM-5 cloud path"},
		}
	}
}

func filterSuggestions(rows []slashSuggestion, query string) []slashSuggestion {
	query = strings.ToLower(strings.TrimSpace(query))
	if query == "" {
		return rows
	}
	filtered := make([]slashSuggestion, 0, len(rows))
	for _, row := range rows {
		value := strings.ToLower(row.Value)
		if strings.HasPrefix(value, query) {
			filtered = append(filtered, row)
		}
	}
	return filtered
}

func (m *model) visibleSlashSuggestions() []slashSuggestion {
	if !m.slashMenuVisible() {
		return nil
	}
	if len(m.slashMenu.Rows) <= maxSlashMenuRows {
		return m.slashMenu.Rows
	}
	start := m.slashMenu.Selected - (maxSlashMenuRows / 2)
	if start < 0 {
		start = 0
	}
	end := start + maxSlashMenuRows
	if end > len(m.slashMenu.Rows) {
		end = len(m.slashMenu.Rows)
		start = end - maxSlashMenuRows
	}
	return m.slashMenu.Rows[start:end]
}
