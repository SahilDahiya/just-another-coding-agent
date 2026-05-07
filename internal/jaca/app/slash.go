package app

import (
	"fmt"
	"sort"
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
	Value        string
	DisplayValue string
	Description  string
	AcceptsArgs  bool
	Current      bool
}

type slashMenuState struct {
	Mode     slashMenuMode
	Command  string
	Rows     []slashSuggestion
	Selected int
}

type slashCommandSpec struct {
	Value                 string
	Description           string
	AcceptsArgs           bool
	AllowedWhileStreaming bool
	ArgSuggestions        func(*model) []slashSuggestion
	Execute               func(*model, string) (tea.Model, tea.Cmd)
}

var slashCommands = []slashCommandSpec{
	{Value: "/login", Description: "Connect ChatGPT or API-key access", AcceptsArgs: true, ArgSuggestions: (*model).loginSlashSuggestions, Execute: (*model).executeLoginSlash},
	{Value: "/model", Description: "Switch active model", AcceptsArgs: true, ArgSuggestions: (*model).modelSlashSuggestions, Execute: (*model).executeModelSlash},
	{Value: "/permission", Description: "Show or switch permission preset", AcceptsArgs: true, ArgSuggestions: (*model).permissionSlashSuggestions, Execute: (*model).executePermissionSlash},
	{Value: "/onboard", Description: "Ask one codebase onboarding question", AcceptsArgs: true, Execute: (*model).executeOnboardSlash},
	{Value: "/approve", Description: "Approve the pending action", AllowedWhileStreaming: true, Execute: (*model).executeApproveSlash},
	{Value: "/deny", Description: "Deny the pending action", AllowedWhileStreaming: true, Execute: (*model).executeDenySlash},
	{Value: "/version", Description: "Show installed and available version info", Execute: (*model).executeVersionSlash},
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
		value := active.Value
		if m.slashMenu.Command == "/model" && strings.TrimSpace(active.DisplayValue) != "" {
			value = active.DisplayValue
		}
		m.textInput.SetValue(fmt.Sprintf("%s %s", m.slashMenu.Command, value))
		m.textInput.CursorEnd()
		m.syncSlashMenu()
	}
}

func (m *model) submitSelectedSlashSuggestion(whileStreaming bool) (tea.Model, tea.Cmd) {
	if !m.slashMenuVisible() {
		return m, nil
	}
	active := m.slashMenu.Rows[m.slashMenu.Selected]
	switch m.slashMenu.Mode {
	case slashMenuCommands:
		if active.AcceptsArgs {
			m.commitSlashSuggestion()
			m.refreshViewport()
			return m, nil
		}
		return m.submitSlashCommand(active.Value, whileStreaming)
	case slashMenuArguments:
		m.commitSlashSuggestion()
		return m.submitSlashCommand(strings.TrimSpace(m.textInput.Value()), whileStreaming)
	default:
		return m, nil
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
	if len(rows) == 1 && !hasTrailingSpace {
		exactValue := strings.EqualFold(rawArg, rows[0].Value)
		exactDisplay := strings.TrimSpace(rows[0].DisplayValue) != "" &&
			normalizeModelSelectionLabel(rawArg) == normalizeModelSelectionLabel(rows[0].DisplayValue)
		if exactValue || exactDisplay {
			return slashMenuState{}
		}
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

func (m *model) modelSlashSuggestions() []slashSuggestion {
	return m.catalogModelSuggestions()
}

func (m *model) loginSlashSuggestions() []slashSuggestion {
	return loginSuggestions(m.authStatus)
}

func (m *model) traceSlashSuggestions() []slashSuggestion {
	return traceSuggestions()
}

func (m *model) permissionSlashSuggestions() []slashSuggestion {
	currentPreset := ""
	if m.permissionState != nil {
		currentPreset = permissionPresetFromState(*m.permissionState)
	}
	return []slashSuggestion{
		{
			Value:       "default",
			Description: "Read and edit files in the current workspace, and run commands. Approval is required for network access or access outside the workspace.",
			Current:     currentPreset == "default",
		},
		{
			Value:       "full_access",
			Description: "Edit files outside this workspace and access the internet without asking for approval. Exercise caution when using.",
			Current:     currentPreset == "full_access",
		},
	}
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
	if whileStreaming && spec.Value != "" && !spec.AllowedWhileStreaming {
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

func (m *model) executePermissionSlash(arg string) (tea.Model, tea.Cmd) {
	return m.handlePermissionCommand(arg)
}

func (m *model) executeApproveSlash(_ string) (tea.Model, tea.Cmd) {
	return m.handleApprovalCommand("approved")
}

func (m *model) executeDenySlash(_ string) (tea.Model, tea.Cmd) {
	return m.handleApprovalCommand("denied")
}

func (m *model) executeVersionSlash(_ string) (tea.Model, tea.Cmd) {
	m.handleVersionCommand()
	m.refreshViewport()
	return m, nil
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

func permissionStateLines(
	state rpc.PermissionState,
	updated bool,
) []string {
	lines := []string{}
	if updated {
		lines = append(lines, "permission state updated")
	}
	preset := permissionPresetFromState(state)
	lines = append(
		lines,
		fmt.Sprintf("permission: %s", preset),
		fmt.Sprintf(
			"effective capabilities: filesystem=%s network=%s isolation=%s approval=%s",
			state.EffectiveCapabilities.FilesystemAccess,
			state.EffectiveCapabilities.NetworkAccess,
			state.EffectiveCapabilities.ExecutionIsolation,
			state.EffectiveCapabilities.ApprovalMode,
		),
	)
	if preset == "custom" {
		lines = append(
			lines,
			fmt.Sprintf("sandbox policy: %s", state.SandboxPolicy.Mode),
			fmt.Sprintf("approval policy: %s", state.ApprovalPolicy.Mode),
		)
	}
	if state.SandboxPolicy.Mode != "danger_full_access" &&
		state.EffectiveCapabilities.FilesystemAccess == "full_access" &&
		state.EffectiveCapabilities.NetworkAccess == "enabled" &&
		state.EffectiveCapabilities.ExecutionIsolation == "unsandboxed" {
		lines = append(
			lines,
			"selected sandbox policy is staged; the restricted local executor backend is not wired yet",
		)
	}
	return lines
}

func permissionPresetFromState(state rpc.PermissionState) string {
	switch {
	case state.SandboxPolicy.Mode == "workspace_write" && state.ApprovalPolicy.Mode == "on_escalation":
		return "default"
	case state.SandboxPolicy.Mode == "danger_full_access" && state.ApprovalPolicy.Mode == "never":
		return "full_access"
	default:
		return "custom"
	}
}

func parsePermissionCommand(
	arg string,
) (*rpc.SandboxPolicy, *rpc.ApprovalPolicy, bool) {
	value := strings.TrimSpace(strings.ToLower(arg))
	if value == "" || value == "show" {
		return nil, nil, true
	}
	switch value {
	case "default":
		return &rpc.SandboxPolicy{Mode: "workspace_write"}, &rpc.ApprovalPolicy{Mode: "on_escalation"}, true
	case "full_access":
		return &rpc.SandboxPolicy{Mode: "danger_full_access"}, &rpc.ApprovalPolicy{Mode: "never"}, true
	default:
		return nil, nil, false
	}
}

func (m *model) handlePermissionCommand(arg string) (tea.Model, tea.Cmd) {
	m.transcript.WriteNote("permission", nil)
	if m.options.Backend == nil {
		m.transcript.WriteError("backend unavailable")
		m.refreshViewport()
		return m, nil
	}
	sandboxPolicy, approvalPolicy, ok := parsePermissionCommand(arg)
	if !ok {
		m.transcript.WriteError(
			"invalid. use /permission to show current mode, or /permission [default|full_access] to switch",
		)
		m.refreshViewport()
		return m, nil
	}
	if sandboxPolicy == nil && approvalPolicy == nil {
		return m, fetchPermissionState(m.options.Backend, m.sessionID, true)
	}
	return m, setPermissionState(
		m.options.Backend,
		m.sessionID,
		sandboxPolicy,
		approvalPolicy,
	)
}

func (m *model) handleApprovalCommand(decision string) (tea.Model, tea.Cmd) {
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
	if m.pendingApproval == nil {
		m.transcript.WriteError("no pending approval request")
		m.refreshViewport()
		return m, nil
	}
	approvalDecision, ok := m.approvalDecisionForIntent(decision)
	if !ok {
		m.transcript.WriteError("no matching approval option")
		m.refreshViewport()
		return m, nil
	}
	return m, submitApprovalDecision(
		m.options.Backend,
		m.sessionID,
		approvalDecision,
	)
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
	type candidate struct {
		row       slashSuggestion
		available bool
	}
	rows := make([]candidate, 0)
	for _, providerCatalog := range catalog.Providers {
		for _, model := range providerCatalog.Models {
			accessState := modelAccessState(
				model.ModelID,
				providerCatalog.Provider,
				authStatus,
			)
			description := model.Description
			if accessState.Label != "" {
				description = fmt.Sprintf("%s [%s]", description, accessState.Label)
			}
			rows = append(rows, candidate{
				row: slashSuggestion{
					Value:        model.ModelID,
					DisplayValue: displayModelName(model.ModelID),
					Description:  description,
				},
				available: accessState.Available,
			})
		}
	}
	sort.SliceStable(rows, func(i int, j int) bool {
		if rows[i].available == rows[j].available {
			return false
		}
		return rows[i].available && !rows[j].available
	})
	suggestions := make([]slashSuggestion, 0, len(rows))
	for _, row := range rows {
		suggestions = append(suggestions, row.row)
	}
	return suggestions
}

type modelAccess struct {
	Label     string
	Available bool
}

func modelAccessState(
	modelID string,
	provider string,
	authStatus *rpc.AuthStatusResponse,
) modelAccess {
	if isOpenAICodexOAuthModel(modelID) {
		if oauthLoggedIn(authStatus, "openai-codex") {
			return modelAccess{Label: "✓", Available: true}
		}
		return modelAccess{Label: "oauth login required"}
	}
	if providerConfiguredForSuggestions(authStatus, provider) {
		return modelAccess{Label: "✓", Available: true}
	}
	return modelAccess{Label: "api-key required"}
}

func loginSuggestions(statuses *rpc.AuthStatusResponse) []slashSuggestion {
	return []slashSuggestion{
		{
			Value:       "openai-codex",
			Description: readyBadgeDescription("ChatGPT subscription", oauthLoggedIn(statuses, "openai-codex")),
		},
		{
			Value:       "openai",
			Description: readyBadgeDescription("OpenAI API key", providerConfiguredForSuggestions(statuses, "openai")),
		},
		{
			Value:       "anthropic",
			Description: readyBadgeDescription("Anthropic API key", providerConfiguredForSuggestions(statuses, "anthropic")),
		},
	}
}

func readyBadgeDescription(label string, ready bool) string {
	if !ready {
		return label
	}
	return label + " [✓]"
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
	normalizedQuery := normalizeModelSelectionLabel(query)
	type candidate struct {
		row   slashSuggestion
		exact bool
	}
	filtered := make([]candidate, 0, len(rows))
	for _, row := range rows {
		value := strings.ToLower(row.Value)
		displayValue := strings.ToLower(strings.TrimSpace(row.DisplayValue))
		normalizedDisplayValue := normalizeModelSelectionLabel(displayValue)
		if strings.HasPrefix(value, query) ||
			(displayValue != "" && strings.HasPrefix(displayValue, query)) ||
			(normalizedDisplayValue != "" && strings.HasPrefix(normalizedDisplayValue, normalizedQuery)) {
			filtered = append(filtered, candidate{
				row: row,
				exact: value == query ||
					displayValue == query ||
					(normalizedDisplayValue != "" && normalizedDisplayValue == normalizedQuery),
			})
		}
	}
	exactRows := make([]slashSuggestion, 0, len(filtered))
	for _, row := range filtered {
		if row.exact {
			exactRows = append(exactRows, row.row)
		}
	}
	if len(exactRows) > 0 {
		return exactRows
	}
	sort.SliceStable(filtered, func(i int, j int) bool {
		if filtered[i].exact == filtered[j].exact {
			return false
		}
		return filtered[i].exact && !filtered[j].exact
	})
	result := make([]slashSuggestion, 0, len(filtered))
	for _, row := range filtered {
		result = append(result, row.row)
	}
	return result
}
