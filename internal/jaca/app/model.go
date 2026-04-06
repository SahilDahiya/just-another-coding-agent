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
	AppVersion            string
	Model                 string
	WorkspaceRoot         string
	SessionsRoot          string
	SessionID             string
	SessionName           string
	ForkedFromSessionID   string
	ForkedFromSessionName string
	Thinking              string
	Backend               Backend
	UpdateCommand         []string
	SkippedUpdateVersion  string
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
	queueControlTimeout  = 30 * time.Second
	authStatusRetryDelay = 750 * time.Millisecond
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

type enqueueRunDoneMsg struct {
	Prompt string
	Mode   string
	Err    error
}

type interruptRunDoneMsg struct {
	PromotedCount int
	Err           error
}

type modelCatalogLoadedMsg struct {
	Catalog rpc.ModelCatalogResponse
	Err     error
}

type authStatusLoadedMsg struct {
	Status rpc.AuthStatusResponse
	Err    error
}

type sessionPreviewLoadedMsg struct {
	Preview rpc.SessionPreviewResponse
	Err     error
}

type authStatusRetryMsg struct{}

type onboardingState struct {
	Active   bool
	Kind     string
	Selected int
}

type sessionState struct {
	sessionID             string
	sessionName           string
	forkedFromSessionID   string
	forkedFromSessionName string
	sessionPreviewLoading bool
	sessionPreviewLoaded  bool
}

type promptState struct {
	promptHistory      []string
	historyIndex       int
	historyDraft       string
	promptFooterNotice string
	slashMenu          slashMenuState
	queuedPreview      queuedPreviewState
}

type queuedPreviewState struct {
	Next  []string
	Later []string
}

type runState struct {
	phase              Phase
	streaming          bool
	activeRunSucceeded bool
	lastInterrupt      time.Time
	activeRunCancel    context.CancelFunc
	runStartTime       time.Time
	lastDeltaTime      time.Time
	pendingAssistant   string
	liveFlushScheduled bool
	asyncCh            chan tea.Msg
	lastUsage          usageSnapshot
}

type layoutState struct {
	width        int
	height       int
	visibleZones int
	motionTick   int
	linePulse    int
}

type backendState struct {
	configErrLogged     bool
	modelCatalog        *rpc.ModelCatalogResponse
	modelCatalogLoading bool
	authStatus          *rpc.AuthStatusResponse
	authStatusLoading   bool
}

type overlayState struct {
	auth                 authState
	startupOnboardingSet bool
	onboarding           onboardingState
	updatePrompt         updatePromptState
}

type installState struct {
	appVersion           string
	skippedUpdateVersion string
}

type model struct {
	options Options
	sessionState
	promptState
	runState
	layoutState
	backendState
	overlayState
	installState
	textInput  textinput.Model
	viewport   viewport.Model
	transcript *Transcript
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
	if options.SessionID != "" {
		startupOnboardingSet = false
		onboarding = onboardingState{}
		lines := []string{fmt.Sprintf("resumed %s", options.SessionID)}
		if options.SessionName != "" {
			lines = []string{
				fmt.Sprintf("resumed %s", options.SessionName),
				fmt.Sprintf("id: %s", options.SessionID),
			}
		}
		if options.ForkedFromSessionID != "" {
			label := options.ForkedFromSessionID
			if options.ForkedFromSessionName != "" {
				label = options.ForkedFromSessionName
			}
			lines = append(lines, fmt.Sprintf("forked from %s", label))
		}
		transcript.WriteNote("session", lines)
	}

	return &model{
		options: options,
		sessionState: sessionState{
			sessionID:             options.SessionID,
			sessionName:           options.SessionName,
			forkedFromSessionID:   options.ForkedFromSessionID,
			forkedFromSessionName: options.ForkedFromSessionName,
		},
		promptState: promptState{
			historyIndex: -1,
		},
		runState: runState{
			phase: PhaseIdle,
		},
		overlayState: overlayState{
			startupOnboardingSet: startupOnboardingSet,
			onboarding:           onboarding,
		},
		installState: installState{
			appVersion:           options.AppVersion,
			skippedUpdateVersion: options.SkippedUpdateVersion,
		},
		textInput:  input,
		viewport:   newViewport(),
		transcript: transcript,
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
	if cmd := m.requestSessionPreview(); cmd != nil {
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
		m.sessionName = ""
		m.forkedFromSessionID = ""
		m.forkedFromSessionName = ""
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
			contextWindow := msg.Event.NextRequestContextUsed
			if contextWindow == nil {
				contextWindow = msg.Event.ContextWindowUsed
			}
			m.activeRunSucceeded = true
			m.lastUsage = usageSnapshot{
				InputTokens:   msg.Event.InputTokens,
				OutputTokens:  msg.Event.OutputTokens,
				TotalTokens:   msg.Event.TotalTokens,
				ContextWindow: contextWindow,
			}
		}
		if msg.Event.Type == "session_queue_state" {
			m.queuedPreview.Next = append([]string{}, msg.Event.NextPrompts...)
			m.queuedPreview.Later = append([]string{}, msg.Event.LaterPrompts...)
		}
		if msg.Event.Type == "run_failed" && msg.Event.ErrorType != "CancelledError" {
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
	case enqueueRunDoneMsg:
		if msg.Err != nil {
			m.transcript.WriteError(msg.Err.Error())
			m.textInput.SetValue(msg.Prompt)
			m.textInput.CursorEnd()
		}
		m.refreshViewport()
		return m, nil
	case interruptRunDoneMsg:
		if msg.Err != nil {
			m.transcript.WriteError(msg.Err.Error())
			m.refreshViewport()
			return m, nil
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
			if errors.Is(msg.Err, context.DeadlineExceeded) || errors.Is(msg.Err, context.Canceled) {
				return m, waitForAuthStatusRetry()
			}
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
	case sessionPreviewLoadedMsg:
		m.sessionPreviewLoading = false
		m.sessionPreviewLoaded = true
		if msg.Err != nil {
			if !errors.Is(msg.Err, context.DeadlineExceeded) && !errors.Is(msg.Err, context.Canceled) {
				m.transcript.WriteError(fmt.Sprintf("session preview: %v", msg.Err))
				m.refreshViewport()
			}
			return m, nil
		}
		m.transcript.ApplySessionPreview(msg.Preview)
		m.refreshViewport()
		return m, nil
	case authStatusRetryMsg:
		return m, m.requestAuthStatus()
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
		SessionName:    m.sessionName,
		MotionTick:     m.motionTick,
		Transcript:     m.viewport.View(),
		PromptValue:    m.promptView(),
		PromptFooter:   m.currentPromptFooter(),
		RunElapsed:     elapsed,
		Usage:          m.lastUsage,
		QueuedNext:     append([]string{}, m.queuedPreview.Next...),
		QueuedLater:    append([]string{}, m.queuedPreview.Later...),
		LinePulse:      m.linePulse,
		SinceLastDelta: sinceLastDelta,
		DetachedLive:   m.streaming && !m.viewport.AtBottom(),
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
			Title:       authOverlayTitle(m.auth.Storage),
			Provider:    m.auth.Provider,
			SecretLabel: authSecretLabel(m.auth.Provider),
			InputValue:  m.textInput.View(),
			HelpLines:   authSetupLinesForStorage(m.auth.Provider, m.auth.Storage, m.auth.FileStorePath),
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
		if m.streaming {
			return m.handleQueueFollowUp()
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
		if m.streaming {
			return m.handleQueueSteer()
		}
		return m.handleEnter()
	}
	var cmd tea.Cmd
	m.clearInterruptGuidance()
	m.textInput, cmd = m.textInput.Update(msg)
	if m.auth.Active || m.streaming {
		m.clearSlashMenu()
	} else {
		m.syncSlashMenu()
	}
	m.refreshViewport()
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
		m.promptFooterNotice = "Conversation interrupted."
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
		pendingPrompt := m.auth.PendingPrompt
		m.endAuthFlow()
		if returnKind != "" {
			m.onboarding = onboardingState{
				Active:   true,
				Kind:     returnKind,
				Selected: onboardingSelectionForProvider(provider),
			}
		} else {
			m.restorePendingPrompt(pendingPrompt)
		}
		m.refreshViewport()
		return m, nil
	}
	if m.slashMenuVisible() {
		m.clearSlashMenu()
		m.refreshViewport()
		return m, nil
	}
	if m.streaming {
		m.promptFooterNotice = "Conversation interrupted."
		m.refreshViewport()
		if m.sessionID == "" {
			return m, nil
		}
		return m, interruptRun(
			m.options.Backend,
			m.sessionID,
			true,
		)
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

func (m *model) handleEnter() (tea.Model, tea.Cmd) {
	prompt := strings.TrimSpace(m.textInput.Value())
	if prompt == "" || m.streaming {
		return m, nil
	}
	if m.auth.Active {
		return m.handleAuthEnter()
	}
	if strings.HasPrefix(prompt, "/") {
		m.recordPromptHistory(prompt)
		m.textInput.SetValue("")
		m.clearSlashMenu()
		m.clearInterruptGuidance()
		return m.handleSlashCommand(prompt)
	}
	provider := m.currentProvider()
	hasCreds, err := m.providerHasCredentialsFresh(provider)
	if err != nil {
		m.transcript.WriteError(err.Error())
		m.refreshViewport()
		return m, nil
	}
	if !hasCreds {
		if err := m.startCredentialSetup(provider, "", "", "", prompt); err != nil {
			m.transcript.WriteError(err.Error())
		}
		m.refreshViewport()
		return m, nil
	}
	m.recordPromptHistory(prompt)
	m.textInput.SetValue("")
	m.clearSlashMenu()
	m.clearInterruptGuidance()
	m.transcript.WriteUserTurn(prompt)
	m.phase = PhaseStreaming
	m.streaming = true
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

func (m *model) handleQueueFollowUp() (tea.Model, tea.Cmd) {
	if !m.streaming {
		return m, nil
	}
	prompt := strings.TrimSpace(m.textInput.Value())
	if prompt == "" {
		return m, nil
	}
	if m.sessionID == "" {
		m.transcript.WriteError("follow-up unavailable until the active session is ready")
		m.refreshViewport()
		return m, nil
	}
	m.textInput.SetValue("")
	m.clearSlashMenu()
	m.refreshViewport()
	return m, enqueueRun(m.options.Backend, m.sessionID, prompt, "later")
}

func (m *model) handleQueueSteer() (tea.Model, tea.Cmd) {
	prompt := strings.TrimSpace(m.textInput.Value())
	if prompt == "" {
		return m, nil
	}
	if m.sessionID == "" {
		m.transcript.WriteError("steering unavailable until the active session is ready")
		m.refreshViewport()
		return m, nil
	}
	m.textInput.SetValue("")
	m.clearSlashMenu()
	m.refreshViewport()
	return m, enqueueRun(m.options.Backend, m.sessionID, prompt, "next")
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
			// User-initiated interrupt: attempt graceful shutdown but
			// never surface shutdown errors — the run was cancelled
			// intentionally and the session remains usable.
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 1200*time.Millisecond)
			defer cancel()
			_ = backend.Interrupt(shutdownCtx)
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

func (m *model) requestSessionPreview() tea.Cmd {
	if m.options.SessionID == "" || m.sessionPreviewLoaded || m.sessionPreviewLoading || m.options.Backend == nil {
		return nil
	}
	m.sessionPreviewLoading = true
	return fetchSessionPreview(m.options.Backend, m.options.SessionID)
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

func fetchSessionPreview(backend Backend, sessionID string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
		defer cancel()
		preview, err := backend.SessionPreview(ctx, sessionID)
		return sessionPreviewLoadedMsg{Preview: preview, Err: err}
	}
}

func enqueueRun(backend Backend, sessionID string, prompt string, mode string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), queueControlTimeout)
		defer cancel()
		_, err := backend.EnqueueRun(ctx, sessionID, prompt, mode)
		return enqueueRunDoneMsg{Prompt: prompt, Mode: mode, Err: err}
	}
}

func interruptRun(
	backend Backend,
	sessionID string,
	promoteQueuedSteer bool,
) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), queueControlTimeout)
		defer cancel()
		response, err := backend.InterruptRun(
			ctx,
			sessionID,
			promoteQueuedSteer,
		)
		return interruptRunDoneMsg{
			PromotedCount: response.PromotedCount,
			Err:           err,
		}
	}
}

func waitForAuthStatusRetry() tea.Cmd {
	return tea.Tick(authStatusRetryDelay, func(time.Time) tea.Msg {
		return authStatusRetryMsg{}
	})
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
