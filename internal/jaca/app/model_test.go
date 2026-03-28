package app

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/rpc"
)

func newTestModel() *model {
	m := New(Options{
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
		Thinking:      "medium",
	}).(*model)
	m.transcript = NewTranscript()
	m.viewport = newViewport()
	m.viewport.Width = 80
	m.viewport.Height = 8
	m.width = 80
	m.height = 12
	m.visibleZones = 3
	m.asyncCh = make(chan tea.Msg)
	return m
}

func TestModelBuffersAssistantDeltasUntilLiveFlush(t *testing.T) {
	m := newTestModel()

	m.Update(runEventMsg{Event: rpc.RunEvent{Type: "assistant_text_delta", Delta: "Hello"}})
	m.Update(runEventMsg{Event: rpc.RunEvent{Type: "assistant_text_delta", Delta: " world"}})

	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "Hello world") {
		t.Fatalf("assistant delta rendered before flush: %q", got)
	}
	if m.pendingAssistant != "Hello world" {
		t.Fatalf("pendingAssistant = %q, want %q", m.pendingAssistant, "Hello world")
	}
	if !m.liveFlushScheduled {
		t.Fatal("expected live flush to be scheduled")
	}

	m.Update(liveFlushMsg{})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "Hello world") {
		t.Fatalf("assistant delta missing after flush: %q", rendered)
	}
	if m.pendingAssistant != "" {
		t.Fatalf("pendingAssistant = %q, want empty", m.pendingAssistant)
	}
}

func TestRefreshViewportPreservesManualScrollPosition(t *testing.T) {
	m := newTestModel()
	for i := 0; i < 30; i++ {
		m.transcript.WriteLine(fmt.Sprintf("line %02d", i))
	}
	m.refreshViewport()
	m.viewport.GotoTop()

	if m.viewport.YOffset != 0 {
		t.Fatalf("YOffset before refresh = %d, want 0", m.viewport.YOffset)
	}

	m.transcript.WriteLine("new bottom line")
	m.refreshViewport()

	if m.viewport.YOffset != 0 {
		t.Fatalf("refreshViewport() moved manual scroll position to %d", m.viewport.YOffset)
	}
}

func TestMouseWheelScrollsViewport(t *testing.T) {
	m := newTestModel()
	for i := 0; i < 30; i++ {
		m.transcript.WriteLine(fmt.Sprintf("line %02d", i))
	}
	m.refreshViewport()
	m.viewport.GotoTop()

	m.Update(tea.MouseMsg(tea.MouseEvent{
		Action: tea.MouseActionPress,
		Button: tea.MouseButtonWheelDown,
		Type:   tea.MouseWheelDown,
	}))

	if m.viewport.YOffset == 0 {
		t.Fatal("expected mouse wheel to scroll viewport")
	}
}

func TestCtrlCWhileStreamingShowsInterruptGuidanceInPromptFooter(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming

	m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})

	if m.phase != PhaseStreaming {
		t.Fatalf("phase = %q, want %q", m.phase, PhaseStreaming)
	}

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Conversation interrupted. Esc again to edit previous message.") {
		t.Fatalf("view missing interrupt guidance: %q", rendered)
	}

	m.Update(runEventMsg{Event: rpc.RunEvent{Type: "assistant_text_delta", Delta: "still running"}})
	m.Update(liveFlushMsg{})
	rendered = stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "still running") {
		t.Fatalf("streaming output was dropped after ctrl+c: %q", rendered)
	}
}

func TestCtrlCIsNonDestructiveWhenPromptHasText(t *testing.T) {
	m := newTestModel()
	m.textInput.SetValue("draft prompt")

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	if cmd != nil {
		t.Fatalf("expected no command, got %v", cmd)
	}
	m = updated.(*model)

	if got := m.textInput.Value(); got != "draft prompt" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "draft prompt")
	}
	if m.phase != PhaseIdle {
		t.Fatalf("phase = %q, want %q", m.phase, PhaseIdle)
	}
}

func TestEscWhileStreamingWritesInterruptGuidance(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming
	canceled := false
	m.activeRunCancel = func() {
		canceled = true
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd != nil {
		t.Fatalf("expected no command, got %v", cmd)
	}
	m = updated.(*model)

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Conversation interrupted. Esc again to edit previous message.") {
		t.Fatalf("missing interrupt guidance in prompt footer: %q", rendered)
	}
	if !canceled {
		t.Fatal("expected first escape to request run cancellation")
	}
}

func TestSecondEscLoadsPreviousPromptIntoComposer(t *testing.T) {
	m := newTestModel()
	m.promptHistory = []string{"first prompt", "previous prompt"}
	m.historyIndex = -1
	m.textInput.SetValue("")
	m.streaming = true
	m.phase = PhaseStreaming

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if !strings.Contains(stripANSI(m.View()), "Esc again to edit previous message.") {
		t.Fatalf("first escape did not arm edit-previous flow: %q", stripANSI(m.View()))
	}

	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "previous prompt" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "previous prompt")
	}
}

func TestEscClearsPromptWhenIdle(t *testing.T) {
	m := newTestModel()
	m.textInput.SetValue("draft prompt")

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "" {
		t.Fatalf("textInput.Value() = %q, want empty", got)
	}
}

func TestCtrlCWhileStreamingDoesNotRequestRunCancellation(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming
	canceled := false
	m.activeRunCancel = func() {
		canceled = true
	}

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated.(*model)

	if canceled {
		t.Fatal("expected ctrl+c to remain copy-safe while streaming")
	}
	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Conversation interrupted. Esc again to edit previous message.") {
		t.Fatalf("missing interrupt guidance in prompt footer: %q", rendered)
	}
}
