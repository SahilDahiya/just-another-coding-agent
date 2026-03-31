package app

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

type Options struct {
	AppVersion           string
	Model                string
	WorkspaceRoot        string
	SessionsRoot         string
	Thinking             string
	Backend              Backend
	UpdateCommand        []string
	SkippedUpdateVersion string
}

type Phase string

const (
	PhaseIdle       Phase = "idle"
	PhaseStreaming  Phase = "streaming"
	PhaseCompleted  Phase = "completed"
	PhaseError      Phase = "error"
	PhaseCompacting Phase = "compacting"
)

const (
	startupRevealDelay   = 50 * time.Millisecond
	liveFlushDelay       = 50 * time.Millisecond
	motionTickDelay      = 140 * time.Millisecond
	completionSettle     = 850 * time.Millisecond
	doubleInterruptDelay = 2 * time.Second
	modelCatalogTimeout  = 8 * time.Second
)

type startupTickMsg struct{}
type liveFlushMsg struct{}
type motionTickMsg struct{}
type phaseResetMsg struct{}

type sessionCreatedMsg struct {
	SessionID string
	Err       error
}

type runEventMsg struct {
	Event rpc.RunEvent
	Err   error
	Done  bool
}

type compactDoneMsg struct {
	Err error
}

type modelCatalogLoadedMsg struct {
	Catalog rpc.ModelCatalogResponse
	Err     error
}

type authStatusLoadedMsg struct {
	Status rpc.AuthStatusResponse
	Err    error
}

type onboardingState struct {
	Active   bool
	Kind     string
	Selected int
}

type model struct {
	options              Options
	phase                Phase
	sessionID            string
	textInput            textinput.Model
	viewport             viewport.Model
	transcript           *Transcript
	width                int
	height               int
	visibleZones         int
	motionTick           int
	streaming            bool
	activeRunSucceeded   bool
	promptHistory        []string
	historyIndex         int
	historyDraft         string
	lastInterrupt        time.Time
	activeRunCancel      context.CancelFunc
	editPreviousArmed    bool
	promptFooterNotice   string
	runStartTime         time.Time
	lastDeltaTime        time.Time
	linePulse            int
	pendingAssistant     string
	liveFlushScheduled   bool
	asyncCh              chan tea.Msg
	slashMenu            slashMenuState
	auth                 authState
	configErrLogged      bool
	lastInputTokens      *int
	lastOutputTokens     *int
	lastTotalTokens      *int
	lastContextWindow    *float64
	modelCatalog         *rpc.ModelCatalogResponse
	modelCatalogLoading  bool
	authStatus           *rpc.AuthStatusResponse
	authStatusLoading    bool
	startupOnboardingSet bool
	onboarding           onboardingState
	appVersion           string
	skippedUpdateVersion string
	updatePrompt         updatePromptState
}

func New(options Options) tea.Model {
	input := textinput.New()
	input.Prompt = ""
	input.Placeholder = ""
	input.Focus()
	input.CharLimit = 0
	input.Width = 80
	input.Cursor.SetMode(cursor.CursorStatic)

	transcript := NewTranscript()
	transcript.WriteStartupBanner(options.AppVersion, options.Model, options.WorkspaceRoot, options.Thinking)
	startupOnboardingSet, onboarding := initialOnboardingState()

	return &model{
		options:              options,
		phase:                PhaseIdle,
		textInput:            input,
		viewport:             newViewport(),
		transcript:           transcript,
		historyIndex:         -1,
		startupOnboardingSet: startupOnboardingSet,
		onboarding:           onboarding,
		appVersion:           options.AppVersion,
		skippedUpdateVersion: options.SkippedUpdateVersion,
	}
}

func initialOnboardingState() (bool, onboardingState) {
	cfg, err := config.Load()
	if err != nil {
		return false, onboardingState{}
	}
	if strings.TrimSpace(cfg["default_provider"]) != "" {
		return false, onboardingState{}
	}
	return true, onboardingState{Active: true, Kind: "provider", Selected: 0}
}

func (m *model) Init() tea.Cmd {
	cmds := []tea.Cmd{
		waitForStartupTick(),
		waitForMotionTick(),
	}
	if cmd := m.requestModelCatalog(); cmd != nil {
		cmds = append(cmds, cmd)
	}
	if cmd := m.requestAuthStatus(); cmd != nil {
		cmds = append(cmds, cmd)
	}
	if len(m.options.UpdateCommand) > 0 && m.options.AppVersion != "" {
		cmds = append(cmds, fetchUpdatePrompt(m.options.AppVersion, m.options.UpdateCommand))
	}
	return tea.Batch(cmds...)
}

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.textInput.Width = max(0, msg.Width-4)
		m.viewport.Width = msg.Width
		m.transcript.Width = msg.Width
		m.refreshViewport()
		return m, nil
	case startupTickMsg:
		if m.visibleZones < 2 {
			m.visibleZones++
			m.refreshViewport()
			if m.visibleZones < 2 {
				return m, waitForStartupTick()
			}
		}
		return m, nil
	case motionTickMsg:
		m.motionTick++
		m.transcript.MotionTick = m.motionTick
		if m.linePulse > 0 {
			m.linePulse--
		}
		m.transcript.RefreshLiveMarker()
		m.refreshViewport()
		return m, waitForMotionTick()
	case liveFlushMsg:
		m.liveFlushScheduled = false
		m.flushPendingAssistantDelta()
		return m, nil
	case phaseResetMsg:
		if !m.streaming && m.phase == PhaseCompleted {
			m.phase = PhaseIdle
		}
		return m, nil
	case sessionCreatedMsg:
		if msg.Err != nil {
			m.phase = PhaseError
			m.transcript.WriteError(msg.Err.Error())
			m.streaming = false
			m.textInput.Focus()
			m.activeRunCancel = nil
			m.lastInterrupt = time.Time{}
			m.refreshViewport()
			return m, nil
		}
		m.sessionID = msg.SessionID
		return m, listenAsync(m.asyncCh)
	case runEventMsg:
		if msg.Err != nil {
			m.flushPendingAssistantDelta()
			m.streaming = false
			m.textInput.Focus()
			m.activeRunCancel = nil
			m.phase = PhaseError
			m.lastInterrupt = time.Time{}
			m.transcript.WriteError(msg.Err.Error())
			m.refreshViewport()
			return m, nil
		}
		if msg.Done {
			m.flushPendingAssistantDelta()
			m.streaming = false
			m.textInput.Focus()
			m.activeRunCancel = nil
			m.lastInterrupt = time.Time{}
			cmd := tea.Cmd(nil)
			switch {
			case m.phase == PhaseError:
			case m.activeRunSucceeded:
				m.phase = PhaseCompleted
				cmd = tea.Tick(completionSettle, func(time.Time) tea.Msg { return phaseResetMsg{} })
			default:
				m.phase = PhaseIdle
			}
			m.activeRunSucceeded = false
			m.refreshViewport()
			return m, cmd
		}
		if msg.Event.Type == "session_compaction_started" {
			m.phase = PhaseCompacting
		}
		if msg.Event.Type == "session_compaction_completed" && m.streaming {
			m.phase = PhaseStreaming
		}
		if msg.Event.Type == "run_succeeded" {
			m.activeRunSucceeded = true
			m.lastInputTokens = msg.Event.InputTokens
			m.lastOutputTokens = msg.Event.OutputTokens
			m.lastTotalTokens = msg.Event.TotalTokens
			m.lastContextWindow = msg.Event.ContextWindowUsed
		}
		if msg.Event.Type == "run_failed" {
			m.phase = PhaseError
		}
		if msg.Event.Type == "assistant_text_delta" {
			m.pendingAssistant += msg.Event.Delta
			m.lastDeltaTime = time.Now()
			m.linePulse = 3
			return m, tea.Batch(listenAsync(m.asyncCh), m.scheduleLiveFlush())
		}
		m.flushPendingAssistantDelta()
		m.transcript.ApplyRunEvent(msg.Event)
		m.refreshViewport()
		return m, listenAsync(m.asyncCh)
	case compactDoneMsg:
		m.streaming = false
		m.textInput.Focus()
		if msg.Err != nil {
			m.phase = PhaseError
			m.transcript.WriteError(fmt.Sprintf("compaction failed: %v", msg.Err))
		} else {
			m.phase = PhaseIdle
			m.transcript.WriteLine("session compacted")
		}
		m.refreshViewport()
		return m, nil
	case modelCatalogLoadedMsg:
		m.modelCatalogLoading = false
		if msg.Err != nil {
			if !errors.Is(msg.Err, context.DeadlineExceeded) && !errors.Is(msg.Err, context.Canceled) {
				m.transcript.WriteError(fmt.Sprintf("model catalog: %v", msg.Err))
				m.refreshViewport()
			}
			return m, nil
		}
		catalog := msg.Catalog
		m.modelCatalog = &catalog
		m.maybeStartOnboarding()
		m.syncSlashMenu()
		m.refreshViewport()
		return m, nil
	case authStatusLoadedMsg:
		m.authStatusLoading = false
		if msg.Err != nil {
			if !errors.Is(msg.Err, context.DeadlineExceeded) && !errors.Is(msg.Err, context.Canceled) {
				m.transcript.WriteError(fmt.Sprintf("auth status: %v", msg.Err))
				m.refreshViewport()
			}
			return m, nil
		}
		status := msg.Status
		m.authStatus = &status
		m.maybeStartOnboarding()
		m.refreshViewport()
		return m, nil
	case updateCheckMsg:
		if msg.Err != nil || msg.LatestVersion == "" || msg.LatestVersion == m.skippedUpdateVersion {
			return m, nil
		}
		m.updatePrompt = updatePromptState{
			Active:         true,
			CurrentVersion: m.appVersion,
			LatestVersion:  msg.LatestVersion,
			Command:        append([]string(nil), msg.Command...),
		}
		m.refreshViewport()
		return m, nil
	case updateRunMsg:
		latestVersion := m.updatePrompt.LatestVersion
		m.updatePrompt = updatePromptState{}
		m.transcript.WriteNote("update", []string{fmt.Sprintf("ran: %s", strings.Join(msg.Command, " "))})
		if msg.Err != nil {
			m.transcript.WriteError(fmt.Sprintf("update failed: %v", msg.Err))
		} else {
			m.transcript.WriteLine(fmt.Sprintf("updated to %s", latestVersion))
			m.transcript.WriteLine("restart jaca to use the new version")
			if err := saveSkippedUpdateVersion(""); err == nil {
				m.skippedUpdateVersion = ""
			}
		}
		m.refreshViewport()
		return m, nil
	case tea.MouseMsg:
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m, cmd
	case tea.KeyMsg:
		return m.handleKey(msg)
	}

	var cmd tea.Cmd
	m.textInput, cmd = m.textInput.Update(msg)
	return m, cmd
}

func (m *model) View() string {
	vm := m.currentViewModel()
	return renderView(vm)
}

func (m *model) currentViewModel() viewModel {
	var elapsed time.Duration
	if m.streaming && !m.runStartTime.IsZero() {
		elapsed = time.Since(m.runStartTime)
	}
	var sinceLastDelta time.Duration
	if m.streaming && !m.lastDeltaTime.IsZero() {
		sinceLastDelta = time.Since(m.lastDeltaTime)
	}
	return viewModel{
		Phase:          m.phase,
		Width:          m.width,
		Height:         m.height,
		Model:          m.options.Model,
		WorkspaceRoot:  m.options.WorkspaceRoot,
		Thinking:       m.options.Thinking,
		SessionID:      m.sessionID,
		MotionTick:     m.motionTick,
		Transcript:     m.viewport.View(),
		PromptValue:    m.promptView(),
		PromptFooter:   m.currentPromptFooter(),
		RunElapsed:     elapsed,
		InputTokens:    m.lastInputTokens,
		OutputTokens:   m.lastOutputTokens,
		TotalTokens:    m.lastTotalTokens,
		ContextWindow:  m.lastContextWindow,
		LinePulse:      m.linePulse,
		SinceLastDelta: sinceLastDelta,
		VisibleZones:   m.visibleZones,
		SlashMenu:      m.slashMenu,
		UpdatePrompt:   m.updatePrompt,
		Onboarding: onboardingOverlayView{
			Active:      m.onboarding.Active,
			Selected:    m.onboarding.Selected,
			Title:       m.onboardingTitle(),
			OptionLines: m.onboardingOptionLines(),
			HelpLines:   m.onboardingHelpLines(),
		},
		Auth: authOverlayView{
			Active:      m.auth.Active,
			Provider:    m.auth.Provider,
			SecretLabel: authSecretLabel(m.auth.Provider),
			InputValue:  m.textInput.View(),
			HelpLines:   authSetupLines(m.auth.Provider),
		},
	}
}

func (m *model) currentPromptFooter() string {
	if m.promptFooterNotice != "" {
		return m.promptFooterNotice
	}
	if m.onboarding.Active {
		return ""
	}
	if m.shouldShowFirstRunPromptAssist() {
		return "first-time setup: tab to choose a provider, or /model ollama:<local-model> for local Ollama"
	}
	return ""
}

func (m *model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.onboarding.Active {
		return m.handleOnboardingKey(msg)
	}
	if m.updatePrompt.Active {
		return m.handleUpdatePromptKey(msg)
	}
	switch msg.String() {
	case "ctrl+c":
		return m.handleInterrupt()
	case "esc":
		return m.handleEscape()
	case "up":
		if m.auth.Active {
			return m, nil
		}
		if m.slashMenuVisible() {
			m.moveSlashSelection(-1)
			return m, nil
		}
		m.historyPrevious()
		m.refreshViewport()
		return m, nil
	case "down":
		if m.auth.Active {
			return m, nil
		}
		if m.slashMenuVisible() {
			m.moveSlashSelection(1)
			return m, nil
		}
		m.historyNext()
		m.refreshViewport()
		return m, nil
	case "pgup":
		m.viewport.HalfViewUp()
		return m, nil
	case "pgdown":
		m.viewport.HalfViewDown()
		return m, nil
	case "home":
		m.viewport.GotoTop()
		return m, nil
	case "end":
		m.viewport.GotoBottom()
		return m, nil
	case "ctrl+u":
		if !m.streaming {
			m.textInput.SetValue("")
			if !m.auth.Active {
				m.resetHistoryNavigation()
			}
			m.clearInterruptGuidance()
			m.clearSlashMenu()
		}
		return m, nil
	case "tab":
		if m.slashMenuVisible() {
			m.commitSlashSuggestion()
			m.refreshViewport()
			return m, nil
		}
		if m.shouldShowFirstRunPromptAssist() && strings.TrimSpace(m.textInput.Value()) == "" {
			m.textInput.SetValue("/provider ")
			m.textInput.CursorEnd()
			m.syncSlashMenu()
			m.refreshViewport()
			return m, nil
		}
		return m, nil
	case "enter":
		if m.slashMenuVisible() {
			m.commitSlashSuggestion()
			m.refreshViewport()
			return m, nil
		}
		return m.handleEnter()
	}
	var cmd tea.Cmd
	if !m.streaming {
		m.clearInterruptGuidance()
		m.textInput, cmd = m.textInput.Update(msg)
		if m.auth.Active {
			m.clearSlashMenu()
		} else {
			m.syncSlashMenu()
		}
		m.refreshViewport()
	}
	return m, cmd
}

func (m *model) handleUpdatePromptKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.updatePrompt.Running {
		return m, nil
	}
	switch msg.String() {
	case "up":
		if m.updatePrompt.Selected > 0 {
			m.updatePrompt.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down", "tab":
		if m.updatePrompt.Selected < len(m.updatePrompt.options())-1 {
			m.updatePrompt.Selected++
		} else {
			m.updatePrompt.Selected = 0
		}
		m.refreshViewport()
		return m, nil
	case "esc":
		m.updatePrompt.Active = false
		m.refreshViewport()
		return m, nil
	case "enter":
		return m.handleUpdatePromptSelection()
	default:
		return m, nil
	}
}

func (m *model) handleUpdatePromptSelection() (tea.Model, tea.Cmd) {
	switch m.updatePrompt.Selected {
	case 0:
		m.updatePrompt.Running = true
		m.refreshViewport()
		return m, runInstalledUpdate(m.updatePrompt.Command)
	case 1:
		m.updatePrompt.Active = false
		m.refreshViewport()
		return m, nil
	case 2:
		if err := saveSkippedUpdateVersion(m.updatePrompt.LatestVersion); err != nil {
			m.transcript.WriteError(fmt.Sprintf("update preference: %v", err))
		} else {
			m.skippedUpdateVersion = m.updatePrompt.LatestVersion
		}
		m.updatePrompt.Active = false
		m.refreshViewport()
		return m, nil
	default:
		return m, nil
	}
}

func (m *model) handleInterrupt() (tea.Model, tea.Cmd) {
	now := time.Now()
	if m.streaming {
		m.promptFooterNotice = "Conversation interrupted. Esc again to edit previous message."
		m.editPreviousArmed = true
		m.refreshViewport()
		return m, nil
	}
	if strings.TrimSpace(m.textInput.Value()) != "" {
		return m, nil
	}
	if now.Sub(m.lastInterrupt) < doubleInterruptDelay {
		return m, tea.Quit
	}
	m.lastInterrupt = now
	m.promptFooterNotice = ""
	m.transcript.WriteNote("warning", []string{"ctrl+c again to quit"})
	m.refreshViewport()
	return m, nil
}

func (m *model) handleEscape() (tea.Model, tea.Cmd) {
	if m.auth.Active {
		returnKind := m.auth.ReturnToOnboardingKind
		provider := m.auth.Provider
		m.endAuthFlow()
		if returnKind != "" {
			m.onboarding = onboardingState{
				Active:   true,
				Kind:     returnKind,
				Selected: onboardingSelectionForProvider(provider),
			}
		}
		m.refreshViewport()
		return m, nil
	}
	if m.slashMenuVisible() {
		m.clearSlashMenu()
		m.refreshViewport()
		return m, nil
	}
	if m.editPreviousArmed {
		m.editPreviousArmed = false
		m.promptFooterNotice = ""
		m.restorePreviousPrompt()
		return m, nil
	}
	if m.streaming {
		m.requestRunCancel()
		m.promptFooterNotice = "Conversation interrupted. Esc again to edit previous message."
		m.editPreviousArmed = true
		m.refreshViewport()
		return m, nil
	}
	if strings.TrimSpace(m.textInput.Value()) != "" {
		m.textInput.SetValue("")
		m.resetHistoryNavigation()
		m.promptFooterNotice = ""
		m.clearSlashMenu()
		m.refreshViewport()
		return m, nil
	}
	return m, nil
}

func (m *model) restorePreviousPrompt() {
	if len(m.promptHistory) == 0 {
		return
	}
	m.textInput.SetValue(m.promptHistory[len(m.promptHistory)-1])
	m.historyIndex = len(m.promptHistory) - 1
	m.syncSlashMenu()
	m.refreshViewport()
}

func (m *model) requestRunCancel() {
	if m.activeRunCancel == nil {
		return
	}
	cancel := m.activeRunCancel
	m.activeRunCancel = nil
	cancel()
}

func (m *model) handleEnter() (tea.Model, tea.Cmd) {
	prompt := strings.TrimSpace(m.textInput.Value())
	if prompt == "" || m.streaming {
		return m, nil
	}
	if m.auth.Active {
		return m.handleAuthEnter()
	}
	m.recordPromptHistory(prompt)
	m.textInput.SetValue("")
	m.clearSlashMenu()
	m.clearInterruptGuidance()
	if strings.HasPrefix(prompt, "/") {
		return m.handleSlashCommand(prompt)
	}
	m.transcript.WriteUserTurn(prompt)
	m.phase = PhaseStreaming
	m.streaming = true
	m.textInput.Blur()
	m.editPreviousArmed = false
	m.lastInterrupt = time.Time{}
	m.activeRunSucceeded = false
	m.runStartTime = time.Now()
	m.refreshViewport()
	m.asyncCh = make(chan tea.Msg, 128)
	backend := m.options.Backend
	sessionID := m.sessionID
	thinking := m.options.Thinking
	runCtx, cancel := context.WithCancel(context.Background())
	m.activeRunCancel = cancel
	go m.runPrompt(runCtx, prompt, sessionID, thinking, backend, m.asyncCh)
	return m, listenAsync(m.asyncCh)
}

func (m *model) handleSlashCommand(command string) (tea.Model, tea.Cmd) {
	parts := strings.Fields(command)
	cmdName := strings.ToLower(parts[0])
	arg := ""
	if len(parts) > 1 {
		arg = strings.TrimSpace(command[len(parts[0]):])
	}
	switch cmdName {
	case "/help":
		m.transcript.WriteHelp()
	case "/model":
		return m.handleModelCommand(arg)
	case "/thinking":
		m.transcript.WriteNote("thinking", nil)
		value := strings.TrimSpace(arg)
		if value == "" {
			current := m.options.Thinking
			if current == "" {
				current = "default"
			}
			m.transcript.WriteLine(fmt.Sprintf("thinking: %s", current))
			break
		}
		switch value {
		case "true", "false", "minimal", "low", "medium", "high", "xhigh":
			m.options.Thinking = value
			m.transcript.WriteLine(fmt.Sprintf("thinking set to %s", value))
		default:
			m.transcript.WriteError("invalid. use: false, high, low, medium, minimal, true, xhigh")
		}
	case "/workspace":
		m.transcript.WriteNote("workspace", nil)
		m.transcript.WriteLine(fmt.Sprintf("workspace: %s", displayPath(m.options.WorkspaceRoot)))
	case "/session":
		m.transcript.WriteNote("session", nil)
		if m.sessionID == "" {
			m.transcript.WriteLine("no active session")
		} else {
			m.transcript.WriteLine(fmt.Sprintf("session: %s", m.sessionID))
		}
	case "/provider":
		m.handleProviderCommand(strings.TrimSpace(arg))
	case "/auth":
		m.handleAuthCommand(strings.TrimSpace(arg))
	case "/trace":
		m.handleTraceCommand(arg)
	case "/compact":
		if m.sessionID == "" {
			m.transcript.WriteNote("compact", nil)
			m.transcript.WriteError("no active session")
			break
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
	case "/new":
		m.transcript.WriteNote("session", nil)
		m.sessionID = ""
		m.phase = PhaseIdle
		m.transcript.WriteLine("session cleared")
	case "/quit":
		return m, tea.Quit
	default:
		m.transcript.WriteNote("command", nil)
		m.transcript.WriteError(fmt.Sprintf("unknown: %s", cmdName))
	}
	m.refreshViewport()
	return m, nil
}

func (m *model) runPrompt(
	ctx context.Context,
	prompt string,
	sessionID string,
	thinking string,
	backend Backend,
	ch chan tea.Msg,
) {
	defer close(ch)
	if sessionID == "" {
		created, err := backend.CreateSession(ctx)
		ch <- sessionCreatedMsg{SessionID: created, Err: err}
		if err != nil {
			return
		}
		sessionID = created
	}
	err := backend.StreamRun(ctx, sessionID, prompt, thinking, func(event rpc.RunEvent) error {
		ch <- runEventMsg{Event: event}
		return nil
	})
	if err != nil {
		if ctx.Err() != nil {
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 1200*time.Millisecond)
			defer cancel()
			if shutdownErr := backend.Interrupt(shutdownCtx); shutdownErr != nil {
				ch <- runEventMsg{Err: shutdownErr}
				return
			}
			ch <- runEventMsg{Done: true}
			return
		}
		ch <- runEventMsg{Err: err}
		return
	}
	ch <- runEventMsg{Done: true}
}

func (m *model) compactSession(sessionID string, backend Backend, ch chan tea.Msg) {
	defer close(ch)
	_, err := backend.CompactSession(context.Background(), sessionID)
	ch <- compactDoneMsg{Err: err}
}

func (m *model) refreshViewport() {
	shouldFollow := m.viewport.AtBottom()
	vm := m.currentViewModel()
	vm.Transcript = ""
	m.viewport.Height = max(1, m.height-promptHeight(vm))
	m.viewport.SetContent(m.transcript.Render())
	if shouldFollow {
		m.viewport.GotoBottom()
	}
}

func (m *model) resetHistoryNavigation() {
	m.historyIndex = -1
	m.historyDraft = ""
}

func (m *model) clearInterruptGuidance() {
	m.editPreviousArmed = false
	m.promptFooterNotice = ""
}

func (m *model) recordPromptHistory(prompt string) {
	m.promptHistory = append(m.promptHistory, prompt)
	m.resetHistoryNavigation()
}

func (m *model) historyPrevious() {
	if m.streaming || len(m.promptHistory) == 0 {
		return
	}
	m.clearInterruptGuidance()
	if m.historyIndex == -1 {
		m.historyDraft = m.textInput.Value()
		m.historyIndex = len(m.promptHistory) - 1
	} else if m.historyIndex > 0 {
		m.historyIndex--
	}
	m.textInput.SetValue(m.promptHistory[m.historyIndex])
	m.textInput.CursorEnd()
	m.syncSlashMenu()
}

func (m *model) historyNext() {
	if m.streaming || m.historyIndex == -1 {
		return
	}
	m.clearInterruptGuidance()
	next := m.historyIndex + 1
	if next >= len(m.promptHistory) {
		draft := m.historyDraft
		m.resetHistoryNavigation()
		m.textInput.SetValue(draft)
		m.textInput.CursorEnd()
		m.syncSlashMenu()
		return
	}
	m.historyIndex = next
	m.textInput.SetValue(m.promptHistory[m.historyIndex])
	m.textInput.CursorEnd()
	m.syncSlashMenu()
}

func listenAsync(ch <-chan tea.Msg) tea.Cmd {
	return func() tea.Msg {
		msg, ok := <-ch
		if !ok {
			return nil
		}
		return msg
	}
}

func (m *model) requestModelCatalog() tea.Cmd {
	if m.modelCatalog != nil || m.modelCatalogLoading || m.options.Backend == nil {
		return nil
	}
	m.modelCatalogLoading = true
	return fetchModelCatalog(m.options.Backend)
}

func (m *model) requestAuthStatus() tea.Cmd {
	if m.authStatus != nil || m.authStatusLoading || m.options.Backend == nil {
		return nil
	}
	m.authStatusLoading = true
	return fetchAuthStatus(m.options.Backend)
}

func fetchModelCatalog(backend Backend) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), modelCatalogTimeout)
		defer cancel()
		catalog, err := backend.ModelCatalog(ctx)
		return modelCatalogLoadedMsg{Catalog: catalog, Err: err}
	}
}

func fetchAuthStatus(backend Backend) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		status, err := backend.AuthStatus(ctx)
		return authStatusLoadedMsg{Status: status, Err: err}
	}
}

func (m *model) scheduleLiveFlush() tea.Cmd {
	if m.liveFlushScheduled {
		return nil
	}
	m.liveFlushScheduled = true
	return tea.Tick(liveFlushDelay, func(time.Time) tea.Msg { return liveFlushMsg{} })
}

func (m *model) flushPendingAssistantDelta() bool {
	if m.pendingAssistant == "" {
		return false
	}
	pending := m.pendingAssistant
	m.pendingAssistant = ""
	m.transcript.appendAssistantDelta(pending)
	m.refreshViewport()
	return true
}

func newViewport() viewport.Model {
	vp := viewport.New(0, 0)
	vp.MouseWheelEnabled = true
	vp.MouseWheelDelta = 3
	return vp
}

func waitForStartupTick() tea.Cmd {
	return tea.Tick(startupRevealDelay, func(time.Time) tea.Msg { return startupTickMsg{} })
}

func waitForMotionTick() tea.Cmd {
	return tea.Tick(motionTickDelay, func(time.Time) tea.Msg { return motionTickMsg{} })
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
