package app

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

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

type slashCommandSpec struct {
	Value          string
	Description    string
	AcceptsArgs    bool
	ArgSuggestions func(*model) []slashSuggestion
	Execute        func(*model, string) (tea.Model, tea.Cmd)
}

var slashCommands = []slashCommandSpec{
	{Value: "/login", Description: "Set up ChatGPT or GitHub Copilot", AcceptsArgs: true, ArgSuggestions: (*model).loginSlashSuggestions, Execute: (*model).executeLoginSlash},
	{Value: "/auth", Description: "Advanced: show auth.json entry for API-key auth", AcceptsArgs: true, ArgSuggestions: (*model).authSlashSuggestions, Execute: (*model).executeAuthSlash},
	{Value: "/model", Description: "Switch active model", AcceptsArgs: true, ArgSuggestions: (*model).modelSlashSuggestions, Execute: (*model).executeModelSlash},
	{Value: "/trace", Description: "Set tracing mode", AcceptsArgs: true, ArgSuggestions: (*model).traceSlashSuggestions, Execute: (*model).executeTraceSlash},
	{Value: "/thinking", Description: "Set thinking effort", AcceptsArgs: true, Execute: (*model).executeThinkingSlash},
	{Value: "/workspace", Description: "Show current workspace", Execute: (*model).executeWorkspaceSlash},
	{Value: "/session", Description: "Show active session", Execute: (*model).executeSessionSlash},
	{Value: "/name", Description: "Name active session", AcceptsArgs: true, Execute: (*model).executeNameSlash},
	{Value: "/compact", Description: "Compact current session", Execute: (*model).executeCompactSlash},
	{Value: "/new", Description: "Clear active session", Execute: (*model).executeNewSlash},
	{Value: "/help", Description: "Show available commands", Execute: (*model).executeHelpSlash},
	{Value: "/quit", Description: "Quit JACA", Execute: (*model).executeQuitSlash},
}

func (m *model) syncSlashMenu() {
	if m.streaming {
		m.clearSlashMenu()
		return
	}
	state := buildSlashMenuState(m.textInput.Value(), m)
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
	case "openai", "anthropic":
		return strings.ToLower(cfg["default_provider"])
	default:
		return m.providerFromModel()
	}
}

func (m *model) providerFromModel() string {
	provider := providerForModel(m.options.Model)
	if provider == "" {
		return "openai"
	}
	return provider
}

func buildSlashMenuState(input string, m *model) slashMenuState {
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
	spec, ok := lookupSlashCommand(commandToken)
	if !ok || spec.ArgSuggestions == nil {
		return slashMenuState{}
	}
	rows := filterSuggestions(spec.ArgSuggestions(m), rawArg)
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
	rows := make([]slashSuggestion, 0, len(slashCommands))
	for _, command := range slashCommands {
		rows = append(rows, slashSuggestion{
			Value:       command.Value,
			Description: command.Description,
			AcceptsArgs: command.AcceptsArgs,
		})
	}
	return rows
}

func authSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "openai", Description: "Show auth.json entry for OpenAI"},
		{Value: "anthropic", Description: "Show auth.json entry for Anthropic"},
	}
}

func traceSuggestions() []slashSuggestion {
	return []slashSuggestion{
		{Value: "off", Description: "Disable tracing"},
		{Value: "local", Description: "Store traces locally"},
		{Value: "logfire", Description: "Send traces to Logfire"},
	}
}

func lookupSlashCommand(name string) (slashCommandSpec, bool) {
	for _, command := range slashCommands {
		if command.Value == name {
			return command, true
		}
	}
	return slashCommandSpec{}, false
}

func (m *model) authSlashSuggestions() []slashSuggestion {
	return authSuggestions()
}

func (m *model) modelSlashSuggestions() []slashSuggestion {
	return m.catalogModelSuggestions()
}

func (m *model) loginSlashSuggestions() []slashSuggestion {
	return openAICodexLoginSuggestions()
}

func (m *model) traceSlashSuggestions() []slashSuggestion {
	return traceSuggestions()
}

func (m *model) handleSlashCommand(command string) (tea.Model, tea.Cmd) {
	spec, cmdName, arg, ok := parseSlashCommand(command)
	if !ok {
		return m, nil
	}
	if spec.Value == "" {
		m.transcript.WriteNote("command", nil)
		m.transcript.WriteError(fmt.Sprintf("unknown: %s", cmdName))
		m.refreshViewport()
		return m, nil
	}
	return spec.Execute(m, arg)
}

func parseSlashCommand(command string) (slashCommandSpec, string, string, bool) {
	parts := strings.Fields(command)
	if len(parts) == 0 {
		return slashCommandSpec{}, "", "", false
	}
	cmdName := strings.ToLower(parts[0])
	arg := ""
	if len(parts) > 1 {
		arg = strings.TrimSpace(command[len(parts[0]):])
	}
	spec, ok := lookupSlashCommand(cmdName)
	if !ok {
		return slashCommandSpec{}, cmdName, arg, true
	}
	return spec, cmdName, arg, true
}

func isSlashInput(value string) bool {
	return strings.HasPrefix(strings.TrimSpace(value), "/")
}

func (m *model) submitSlashCommand(command string, whileStreaming bool) (tea.Model, tea.Cmd) {
	spec, cmdName, arg, ok := parseSlashCommand(command)
	if !ok {
		return m, nil
	}
	if !whileStreaming && m.waitingOAuthLoginBlocksInput() && cmdName != "/login" {
		m.promptFooterNotice = "login in progress; only /login is available until completion or Esc"
		m.refreshViewport()
		return m, nil
	}
	if whileStreaming && spec.Value != "" {
		m.promptFooterNotice = fmt.Sprintf(
			"%s unavailable during an active run; press Esc or wait for idle",
			cmdName,
		)
		m.refreshViewport()
		return m, nil
	}
	m.recordPromptHistory(command)
	m.textInput.SetValue("")
	m.clearSlashMenu()
	m.clearInterruptGuidance()
	if spec.Value == "" {
		m.transcript.WriteNote("command", nil)
		m.transcript.WriteError(fmt.Sprintf("unknown: %s", cmdName))
		m.refreshViewport()
		return m, nil
	}
	return spec.Execute(m, arg)
}

func (m *model) executeHelpSlash(_ string) (tea.Model, tea.Cmd) {
	m.transcript.WriteHelp()
	m.refreshViewport()
	return m, nil
}

func (m *model) executeModelSlash(arg string) (tea.Model, tea.Cmd) {
	return m.handleModelCommand(arg)
}

func (m *model) executeThinkingSlash(arg string) (tea.Model, tea.Cmd) {
	m.transcript.WriteNote("thinking", nil)
	value := strings.TrimSpace(arg)
	if value == "" {
		current := m.options.Thinking
		if current == "" {
			current = "default"
		}
		m.transcript.WriteLine(fmt.Sprintf("thinking: %s", current))
		m.refreshViewport()
		return m, nil
	}
	switch value {
	case "true", "false", "minimal", "low", "medium", "high", "xhigh":
		m.options.Thinking = value
		m.transcript.WriteLine(fmt.Sprintf("thinking set to %s", value))
	default:
		m.transcript.WriteError("invalid. use: false, high, low, medium, minimal, true, xhigh")
	}
	m.refreshViewport()
	return m, nil
}

func (m *model) executeWorkspaceSlash(_ string) (tea.Model, tea.Cmd) {
	m.transcript.WriteNote("workspace", nil)
	m.transcript.WriteLine(fmt.Sprintf("workspace: %s", displayPath(m.options.WorkspaceRoot)))
	m.refreshViewport()
	return m, nil
}

func (m *model) executeSessionSlash(_ string) (tea.Model, tea.Cmd) {
	m.writeSessionInfo()
	m.refreshViewport()
	return m, nil
}

func (m *model) executeNameSlash(arg string) (tea.Model, tea.Cmd) {
	m.handleSessionNameCommand(strings.TrimSpace(arg))
	m.refreshViewport()
	return m, nil
}

func (m *model) executeAuthSlash(arg string) (tea.Model, tea.Cmd) {
	m.handleAuthCommand(strings.TrimSpace(arg))
	m.refreshViewport()
	return m, nil
}

func (m *model) executeLoginSlash(arg string) (tea.Model, tea.Cmd) {
	return m.handleLoginCommand(strings.TrimSpace(arg))
}

func (m *model) executeTraceSlash(arg string) (tea.Model, tea.Cmd) {
	m.handleTraceCommand(arg)
	m.refreshViewport()
	return m, nil
}

func (m *model) executeCompactSlash(_ string) (tea.Model, tea.Cmd) {
	if m.sessionID == "" {
		m.transcript.WriteNote("compact", nil)
		m.transcript.WriteError("no active session")
		m.refreshViewport()
		return m, nil
	}
	m.phase = PhaseCompacting
	m.streaming = true
	m.textInput.Blur()
	m.transcript.WriteNote("compact", nil)
	m.transcript.WriteLine("compacting...")
	m.refreshViewport()
	m.asyncCh = make(chan tea.Msg, 4)
	backend := m.options.Backend
	sessionID := m.sessionID
	go m.compactSession(sessionID, backend, m.asyncCh)
	return m, listenAsync(m.asyncCh)
}

func (m *model) executeNewSlash(_ string) (tea.Model, tea.Cmd) {
	m.transcript.WriteNote("session", nil)
	m.sessionID = ""
	m.sessionName = ""
	m.forkedFromSessionID = ""
	m.forkedFromSessionName = ""
	m.phase = PhaseIdle
	m.transcript.WriteLine("session cleared")
	m.refreshViewport()
	return m, nil
}

func (m *model) executeQuitSlash(_ string) (tea.Model, tea.Cmd) {
	return m, tea.Quit
}

func (m *model) catalogModelSuggestions() []slashSuggestion {
	if m.modelCatalog == nil {
		return nil
	}
	return modelSuggestions(*m.modelCatalog, m.authStatus)
}

func modelSuggestions(
	catalog rpc.ModelCatalogResponse,
	authStatus *rpc.AuthStatusResponse,
) []slashSuggestion {
	if authStatus == nil {
		return nil
	}
	rows := make([]slashSuggestion, 0)
	for _, providerCatalog := range catalog.Providers {
		for _, model := range providerCatalog.Models {
			accessLabel := modelAccessLabel(
				model.ModelID,
				providerCatalog.Provider,
				authStatus,
			)
			description := model.Description
			if accessLabel != "" {
				description = fmt.Sprintf("%s [%s]", description, accessLabel)
			}
			rows = append(rows, slashSuggestion{
				Value:       model.ModelID,
				Description: description,
			})
		}
	}
	return rows
}

func modelAccessLabel(
	modelID string,
	provider string,
	authStatus *rpc.AuthStatusResponse,
) string {
	if isOpenAICodexOAuthModel(modelID) {
		if oauthLoggedIn(authStatus, "openai-codex") {
			return "oauth"
		}
		return "oauth login required"
	}
	if isGitHubCopilotOAuthModel(modelID) {
		if oauthLoggedIn(authStatus, "github-copilot") {
			return "oauth"
		}
		return "oauth login required"
	}
	if providerConfiguredForSuggestions(authStatus, provider) {
		return "api-key"
	}
	return "api-key required"
}

func oauthLoggedIn(statuses *rpc.AuthStatusResponse, provider string) bool {
	if statuses == nil {
		return false
	}
	for _, status := range statuses.OAuthProviders {
		if status.Provider == provider {
			return status.LoggedIn
		}
	}
	return false
}

func providerConfiguredForSuggestions(
	statuses *rpc.AuthStatusResponse,
	provider string,
) bool {
	if statuses == nil {
		return false
	}
	for _, status := range statuses.Providers {
		if status.Provider == provider {
			return status.Configured
		}
	}
	return false
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
