package app

import (
	"fmt"
	"strings"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
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
	state := buildSlashMenuState(
		m.textInput.Value(),
		m.currentProvider(),
		m.catalogModelSuggestions(m.currentProvider()),
	)
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
	if len(state.Rows) == 0 {
		m.clearSlashMenu()
		return
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
	cfg, err := config.Load()
	if err != nil {
		if !m.configErrLogged {
			m.configErrLogged = true
			m.transcript.WriteError(fmt.Sprintf("config: %v", err))
		}
		return m.providerFromModel()
	}
	switch strings.ToLower(cfg["default_provider"]) {
	case "openai", "anthropic", "ollama", "github":
		return strings.ToLower(cfg["default_provider"])
	default:
		return m.providerFromModel()
	}
}

func (m *model) providerFromModel() string {
	switch {
	case strings.HasPrefix(strings.ToLower(m.options.Model), "github:"):
		return "github"
	case strings.HasPrefix(strings.ToLower(m.options.Model), "openai:"):
		return "openai"
	case strings.HasPrefix(strings.ToLower(m.options.Model), "anthropic:"):
		return "anthropic"
	default:
		return "ollama"
	}
}

func buildSlashMenuState(
	input string,
	provider string,
	modelRows []slashSuggestion,
) slashMenuState {
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
	case "/auth":
		rows = filterSuggestions(authSuggestions(), rawArg)
	case "/model":
		rows = filterSuggestions(modelRows, rawArg)
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
		{Value: "/auth", Description: "Authenticate a cloud provider", AcceptsArgs: true},
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
		{Value: "github", Description: "GitHub Models hosted models"},
		{Value: "openai", Description: "OpenAI hosted models"},
		{Value: "anthropic", Description: "Anthropic Claude models"},
	}
}

func authSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "github", Description: "Store GitHub Models token"},
		{Value: "openai", Description: "Store OpenAI API key"},
		{Value: "anthropic", Description: "Store Anthropic API key"},
	}
}

func traceSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "off", Description: "Disable tracing"},
		{Value: "local", Description: "Store traces locally"},
		{Value: "logfire", Description: "Send traces to Logfire"},
	}
}

func (m *model) catalogModelSuggestions(provider string) []slashSuggestion {
	if m.modelCatalog == nil {
		return nil
	}
	return modelSuggestions(*m.modelCatalog, provider)
}

func modelSuggestions(catalog rpc.ModelCatalogResponse, provider string) []slashSuggestion {
	for _, providerCatalog := range catalog.Providers {
		if providerCatalog.Provider != provider {
			continue
		}
		rows := make([]slashSuggestion, 0, len(providerCatalog.Models))
		for _, model := range providerCatalog.Models {
			rows = append(rows, slashSuggestion{
				Value:       model.ModelID,
				Description: model.Description,
			})
		}
		return rows
	}
	return nil
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
