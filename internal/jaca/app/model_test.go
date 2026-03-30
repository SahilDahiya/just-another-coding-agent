package app

import (
	"fmt"
	"os"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/rpc"
)

func intPtr(v int) *int { return &v }

func floatPtr(v float64) *float64 { return &v }

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

func sendKey(m *model, msg tea.KeyMsg) *model {
	updated, _ := m.Update(msg)
	return updated.(*model)
}

func sendRunes(m *model, value string) *model {
	for _, r := range value {
		m = sendKey(m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
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

func TestRefreshViewportDoesNotForceFollowWhileStreamingAfterManualScroll(t *testing.T) {
	m := newTestModel()
	for i := 0; i < 30; i++ {
		m.transcript.WriteLine(fmt.Sprintf("line %02d", i))
	}
	m.refreshViewport()
	m.viewport.GotoTop()
	m.streaming = true

	if m.viewport.YOffset != 0 {
		t.Fatalf("YOffset before refresh = %d, want 0", m.viewport.YOffset)
	}

	m.transcript.WriteLine("new streamed line")
	m.refreshViewport()

	if m.viewport.YOffset != 0 {
		t.Fatalf("refreshViewport() moved manual scroll position during streaming to %d", m.viewport.YOffset)
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

func TestRunSucceededUsageAppearsInFooter(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming

	m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:              "run_succeeded",
		RunID:             "run-1",
		OutputText:        "done",
		InputTokens:       intPtr(120),
		OutputTokens:      intPtr(45),
		TotalTokens:       intPtr(165),
		ContextWindowUsed: floatPtr(0.413),
	}})
	m.Update(runEventMsg{Done: true})

	rendered := stripANSI(m.View())
	for _, want := range []string{"completed", "120 in", "45 out", "165 tok", "41% ctx"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing %q in %q", want, rendered)
		}
	}
}

func TestCompactionLifecycleEventsUpdatePhaseAndTranscript(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming

	updated, _ := m.Update(runEventMsg{Event: rpc.RunEvent{Type: "session_compaction_started"}})
	m = updated.(*model)

	if m.phase != PhaseCompacting {
		t.Fatalf("phase after compaction start = %q, want %q", m.phase, PhaseCompacting)
	}

	updated, _ = m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:              "session_compaction_completed",
		CompactionID:      "compact-1",
		SummarizedThrough: "run-5",
	}})
	m = updated.(*model)

	if m.phase != PhaseStreaming {
		t.Fatalf("phase after compaction complete = %q, want %q", m.phase, PhaseStreaming)
	}

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{"compacting session...", "session compacted"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("transcript missing %q in %q", want, rendered)
		}
	}
}

func TestSlashShowsInlineCommandSuggestions(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/")

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"/provider",
		"/model",
		"/trace",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing slash suggestion %q in %q", want, rendered)
		}
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "/provider") {
		t.Fatalf("transcript changed while browsing slash suggestions: %q", got)
	}
}

func TestTabOnProviderSuggestionCommitsCommandAndShowsProviderOptions(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/pro")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyTab})

	if got := m.textInput.Value(); got != "/provider " {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/provider ")
	}

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"ollama",
		"openai",
		"anthropic",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing provider suggestion %q in %q", want, rendered)
		}
	}
}

func TestSelectingTraceSuggestionCommitsOnlyToPrompt(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/trace loc")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyTab})

	if got := m.textInput.Value(); got != "/trace local" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/trace local")
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "trace") {
		t.Fatalf("transcript changed before slash command execution: %q", got)
	}
}

func TestEscClosesSlashSuggestionsWithoutClearingPrompt(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEsc})

	if got := m.textInput.Value(); got != "/" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/")
	}
	rendered := stripANSI(m.View())
	if strings.Contains(rendered, "/provider") {
		t.Fatalf("slash menu still visible after escape: %q", rendered)
	}
}

func TestDownArrowMovesSlashSelection(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyDown})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "> /auth") {
		t.Fatalf("expected down arrow to move active slash selection in %q", rendered)
	}
}

func TestModelCommandPersistsSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/model openai:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai:gpt-5.4")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"default_model": "openai:gpt-5.4"`) {
		t.Fatalf("config.json missing persisted model: %q", string(data))
	}
	if !strings.Contains(string(data), `"default_provider": "openai"`) {
		t.Fatalf("config.json missing persisted provider: %q", string(data))
	}
}

func TestTraceCommandPersistsMode(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/trace local")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"trace_mode": "local"`) {
		t.Fatalf("config.json missing trace mode: %q", string(data))
	}
}

func TestProviderWithoutCredentialsStartsMaskedAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "auth openai") {
		t.Fatalf("view missing auth footer after provider selection: %q", rendered)
	}
	masked := sendRunes(m, "super-secret")
	rendered = stripANSI(masked.View())
	if strings.Contains(rendered, "super-secret") {
		t.Fatalf("secret leaked into rendered view: %q", rendered)
	}
	if got := masked.promptHistory; len(got) != 1 || got[0] != "/provider openai" {
		t.Fatalf("promptHistory = %#v, want only the non-secret provider command", got)
	}
}

func TestModelWithoutCredentialsStartsMaskedAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/model openai:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "auth openai") {
		t.Fatalf("view missing auth footer after model selection: %q", rendered)
	}
	if got := m.promptHistory; len(got) != 1 || got[0] != "/model openai:gpt-5.4" {
		t.Fatalf("promptHistory = %#v, want only the non-secret model command", got)
	}
}

func TestAuthSubmissionStoresCredentialWithoutLeakingSecret(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "super-secret")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	configText := string(data)
	if !strings.Contains(configText, `"OPENAI_API_KEY": "super-secret"`) {
		t.Fatalf("config.json missing saved credential: %q", configText)
	}
	if !strings.Contains(configText, `"default_provider": "openai"`) {
		t.Fatalf("config.json missing provider selection: %q", configText)
	}
	transcript := stripANSI(m.transcript.Render())
	if strings.Contains(transcript, "super-secret") {
		t.Fatalf("secret leaked into transcript: %q", transcript)
	}
	if len(m.promptHistory) != 1 || m.promptHistory[0] != "/provider openai" {
		t.Fatalf("promptHistory = %#v, want only the provider command", m.promptHistory)
	}
}

func TestAuthSubmissionAppliesPendingModelSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = rpc.NewManager(rpc.BackendConfig{})

	m = sendRunes(m, "/model openai:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "super-secret")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai:gpt-5.4")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	configText := string(data)
	if !strings.Contains(configText, `"default_provider": "openai"`) {
		t.Fatalf("config.json missing provider selection: %q", configText)
	}
	if !strings.Contains(configText, `"default_model": "openai:gpt-5.4"`) {
		t.Fatalf("config.json missing model selection: %q", configText)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "super-secret") {
		t.Fatalf("secret leaked into transcript: %q", stripANSI(m.transcript.Render()))
	}
}
