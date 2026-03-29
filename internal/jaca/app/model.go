package app

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/rpc"
)

type Options struct {
	Model         string
	WorkspaceRoot string
	SessionsRoot  string
	Thinking      string
	Backend       *rpc.Manager
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
	motionTickDelay      = 240 * time.Millisecond
	completionSettle     = 850 * time.Millisecond
	doubleInterruptDelay = 2 * time.Second
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

type model struct {
	options            Options
	phase              Phase
	sessionID          string
	textInput          textinput.Model
	viewport           viewport.Model
	transcript         *Transcript
	width              int
	height             int
	visibleZones       int
	motionTick         int
	streaming          bool
	activeRunSucceeded bool
	promptHistory      []string
	historyIndex       int
	historyDraft       string
	lastInterrupt      time.Time
	activeRunCancel    context.CancelFunc
	editPreviousArmed  bool
	promptFooterNotice string
	runStartTime       time.Time
	lastDeltaTime      time.Time
	linePulse          int
	pendingAssistant   string
	liveFlushScheduled bool
	asyncCh            chan tea.Msg
	slashMenu          slashMenuState
	auth               authState
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
	transcript.WriteStartupBanner(options.Model, options.WorkspaceRoot, options.Thinking)

	return &model{
		options:      options,
		phase:        PhaseIdle,
		textInput:    input,
		viewport:     newViewport(),
		transcript:   transcript,
		historyIndex: -1,
	}
}

func (m *model) Init() tea.Cmd {
	return tea.Batch(
		waitForStartupTick(),
		waitForMotionTick(),
	)
}

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.textInput.Width = max(0, msg.Width-4)
		m.viewport.Width = msg.Width
		m.viewport.Height = max(1, msg.Height-4)
		m.transcript.Width = msg.Width
		m.refreshViewport()
		return m, nil
	case startupTickMsg:
		if m.visibleZones < 2 {
			m.visibleZones++
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
		if msg.Event.Type == "run_succeeded" {
			m.activeRunSucceeded = true
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
		if msg.Err != nil {
			m.phase = PhaseError
			m.transcript.WriteError(fmt.Sprintf("compaction failed: %v", msg.Err))
		} else {
			m.phase = PhaseIdle
			m.transcript.WriteLine("session compacted")
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
	var elapsed time.Duration
	if m.streaming && !m.runStartTime.IsZero() {
		elapsed = time.Since(m.runStartTime)
	}
	var sinceLastDelta time.Duration
	if m.streaming && !m.lastDeltaTime.IsZero() {
		sinceLastDelta = time.Since(m.lastDeltaTime)
	}
	return renderView(viewModel{
		Phase:          m.phase,
		Model:          m.options.Model,
		WorkspaceRoot:  m.options.WorkspaceRoot,
		Thinking:       m.options.Thinking,
		SessionID:      m.sessionID,
		MotionTick:     m.motionTick,
		Transcript:     m.viewport.View(),
		PromptValue:    m.promptView(),
		PromptFooter:   m.promptFooterNotice,
		RunElapsed:     elapsed,
		LinePulse:      m.linePulse,
		SinceLastDelta: sinceLastDelta,
		VisibleZones:  m.visibleZones,
		SlashMenu:     m.slashMenu,
	})
}

func (m *model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
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
			return m, nil
		}
		return m, nil
	case "enter":
		if m.slashMenuVisible() {
			m.commitSlashSuggestion()
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
	}
	return m, cmd
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
		m.endAuthFlow()
		return m, nil
	}
	if m.slashMenuVisible() {
		m.clearSlashMenu()
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
		m.handleModelCommand(arg)
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
	backend *rpc.Manager,
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

func (m *model) compactSession(sessionID string, backend *rpc.Manager, ch chan tea.Msg) {
	defer close(ch)
	_, err := backend.CompactSession(context.Background(), sessionID)
	ch <- compactDoneMsg{Err: err}
}

func (m *model) refreshViewport() {
	shouldFollow := m.streaming || m.viewport.AtBottom()
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
