package app

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

func intPtr(v int) *int { return &v }

func floatPtr(v float64) *float64 { return &v }

type stubBackend struct {
	model              string
	modelCatalog       rpc.ModelCatalogResponse
	modelCatalogErr    error
	authStatuses       map[string]rpc.AuthProviderStatus
	localSecretStore   rpc.LocalSecretStoreStatus
	authStatusErr      error
	setSecretErr       error
	authStatusAfterSet map[string]rpc.AuthProviderStatus
	clearSecretErr     error
	setSessionNameErr  error
	sessionPreview     rpc.SessionPreviewResponse
	sessionPreviewErr  error
	restarts           int
	lastSetSecret      rpc.AuthSetPayload
	lastCleared        string
	lastNamedSession   string
	lastSessionName    string
	lastEnqueuedRun    rpc.RunEnqueuePayload
	lastInterruptedRun rpc.RunInterruptPayload
}

func newStubBackend() *stubBackend {
	return &stubBackend{
		modelCatalog:       *testModelCatalog(),
		authStatuses:       map[string]rpc.AuthProviderStatus{},
		authStatusAfterSet: map[string]rpc.AuthProviderStatus{},
		localSecretStore: rpc.LocalSecretStoreStatus{
			Available:     true,
			FileStorePath: filepath.Join(os.TempDir(), "jaca-secrets.json"),
		},
	}
}

func (b *stubBackend) SetModel(model string) { b.model = model }
func (b *stubBackend) SetEnv(_ []string)     {}
func (b *stubBackend) Restart(_ context.Context) error {
	b.restarts++
	return nil
}
func (b *stubBackend) Shutdown(_ context.Context) error  { return nil }
func (b *stubBackend) Interrupt(_ context.Context) error { return nil }
func (b *stubBackend) InterruptRun(
	_ context.Context,
	sessionID string,
	promoteQueuedSteer bool,
) (rpc.RunInterruptResponse, error) {
	b.lastInterruptedRun = rpc.RunInterruptPayload{
		SessionID:          sessionID,
		PromoteQueuedSteer: promoteQueuedSteer,
	}
	return rpc.RunInterruptResponse{
		SessionID:     sessionID,
		PromotedCount: 1,
	}, nil
}
func (b *stubBackend) CreateSession(_ context.Context) (string, error) {
	return "session", nil
}
func (b *stubBackend) CompactSession(_ context.Context, _ string) (rpc.SessionCompactResponse, error) {
	return rpc.SessionCompactResponse{}, nil
}
func (b *stubBackend) SetSessionName(_ context.Context, sessionID string, name string) (rpc.SessionNameResponse, error) {
	if b.setSessionNameErr != nil {
		return rpc.SessionNameResponse{}, b.setSessionNameErr
	}
	b.lastNamedSession = sessionID
	b.lastSessionName = name
	return rpc.SessionNameResponse{
		SessionID: sessionID,
		Name:      normalizeTestSessionName(name),
	}, nil
}
func (b *stubBackend) SessionPreview(_ context.Context, _ string) (rpc.SessionPreviewResponse, error) {
	if b.sessionPreviewErr != nil {
		return rpc.SessionPreviewResponse{}, b.sessionPreviewErr
	}
	return b.sessionPreview, nil
}
func (b *stubBackend) ModelCatalog(_ context.Context) (rpc.ModelCatalogResponse, error) {
	return b.modelCatalog, b.modelCatalogErr
}
func (b *stubBackend) AuthStatus(_ context.Context) (rpc.AuthStatusResponse, error) {
	if b.authStatusErr != nil {
		return rpc.AuthStatusResponse{}, b.authStatusErr
	}
	providers := []string{"ollama", "openai", "openrouter", "anthropic", "google"}
	statuses := make([]rpc.AuthProviderStatus, 0, len(providers))
	for _, provider := range providers {
		if status, ok := b.authStatuses[provider]; ok {
			statuses = append(statuses, status)
			continue
		}
		statuses = append(statuses, envDerivedAuthStatus(provider))
	}
	return rpc.AuthStatusResponse{
		Providers:        statuses,
		LocalSecretStore: b.localSecretStore,
	}, nil
}
func (b *stubBackend) SetProviderSecret(
	_ context.Context,
	provider string,
	secret string,
	storage string,
) (rpc.AuthSetResponse, error) {
	if b.setSecretErr != nil {
		return rpc.AuthSetResponse{}, b.setSecretErr
	}
	b.lastSetSecret = rpc.AuthSetPayload{
		Provider: provider,
		Secret:   secret,
		Storage:  storage,
	}
	status := rpc.AuthProviderStatus{
		Provider:         provider,
		Configured:       true,
		SecretConfigured: true,
		RequiresSecret:   true,
		Source:           "keychain",
		EnvKey:           envKeyForProvider(provider),
		Reason:           "ok",
	}
	if overridden, ok := b.authStatusAfterSet[provider]; ok {
		status = overridden
	}
	b.authStatuses[provider] = status
	return rpc.AuthSetResponse{Status: status}, nil
}
func (b *stubBackend) ClearProviderSecret(
	_ context.Context,
	provider string,
) (rpc.AuthClearResponse, error) {
	if b.clearSecretErr != nil {
		return rpc.AuthClearResponse{}, b.clearSecretErr
	}
	b.lastCleared = provider
	status := envDerivedAuthStatus(provider)
	b.authStatuses[provider] = status
	return rpc.AuthClearResponse{Status: status}, nil
}
func (b *stubBackend) StreamRun(
	_ context.Context,
	_ string,
	_ string,
	_ string,
	_ func(rpc.RunEvent) error,
) error {
	return nil
}
func (b *stubBackend) EnqueueRun(
	_ context.Context,
	sessionID string,
	prompt string,
	mode string,
) (rpc.RunEnqueueResponse, error) {
	b.lastEnqueuedRun = rpc.RunEnqueuePayload{
		SessionID: sessionID,
		Prompt:    prompt,
		Mode:      mode,
	}
	return rpc.RunEnqueueResponse{SessionID: sessionID, QueuedCount: 1}, nil
}

func envDerivedAuthStatus(provider string) rpc.AuthProviderStatus {
	envKey := ""
	switch provider {
	case "ollama":
		envKey = "OLLAMA_API_KEY"
	case "openai":
		envKey = "OPENAI_API_KEY"
	case "openrouter":
		envKey = "OPENROUTER_API_KEY"
	case "anthropic":
		envKey = "ANTHROPIC_API_KEY"
	case "google":
		envKey = "GOOGLE_API_KEY"
	}
	source := "none"
	secretConfigured := false
	if envKey != "" && strings.TrimSpace(os.Getenv(envKey)) != "" {
		source = "env"
		secretConfigured = true
	}
	requiresSecret := true
	configured := secretConfigured
	reason := "missing_secret"
	switch provider {
	case "ollama":
		baseURL := strings.TrimSpace(os.Getenv("OLLAMA_BASE_URL"))
		if baseURL == "" || strings.Contains(baseURL, "localhost") || strings.Contains(baseURL, "127.0.0.1") {
			requiresSecret = false
			configured = true
			reason = "local_endpoint_no_secret_required"
		} else if secretConfigured {
			reason = "ok"
		}
	case "openai":
		baseURL := strings.TrimSpace(os.Getenv("OPENAI_BASE_URL"))
		if strings.Contains(baseURL, "localhost") || strings.Contains(baseURL, "127.0.0.1") {
			requiresSecret = false
			configured = true
			reason = "local_endpoint_no_secret_required"
		} else if secretConfigured {
			reason = "ok"
		}
	case "openrouter":
		if secretConfigured {
			reason = "ok"
		}
	default:
		if secretConfigured {
			reason = "ok"
		}
	}
	return rpc.AuthProviderStatus{
		Provider:         provider,
		Configured:       configured,
		SecretConfigured: secretConfigured,
		RequiresSecret:   requiresSecret,
		Source:           source,
		EnvKey:           envKey,
		Reason:           reason,
	}
}

func envKeyForProvider(provider string) string {
	switch provider {
	case "ollama":
		return "OLLAMA_API_KEY"
	case "openai":
		return "OPENAI_API_KEY"
	case "openrouter":
		return "OPENROUTER_API_KEY"
	case "anthropic":
		return "ANTHROPIC_API_KEY"
	case "google":
		return "GOOGLE_API_KEY"
	default:
		return ""
	}
}

func normalizeTestSessionName(name string) string {
	re := regexp.MustCompile(`[^a-z0-9]+`)
	return strings.Trim(re.ReplaceAllString(strings.ToLower(strings.TrimSpace(name)), "-"), "-")
}

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
	m.modelCatalog = testModelCatalog()
	m.startupOnboardingSet = false
	m.onboarding = onboardingState{}
	return m
}

func newTestModelWithBackend(backend Backend) *model {
	m := newTestModel()
	m.options.Backend = backend
	return m
}

func TestTypingWhileStreamingUpdatesComposer(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.textInput.Focus()

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "a" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "a")
	}
}

func TestTabWhileStreamingQueuesFollowUp(t *testing.T) {
	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.streaming = true
	m.sessionID = "session-123"
	m.textInput.SetValue("follow up")
	m.textInput.Focus()

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyTab})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("tab while streaming should enqueue a follow-up")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastEnqueuedRun.SessionID != "session-123" {
		t.Fatalf("queued session id = %q, want %q", backend.lastEnqueuedRun.SessionID, "session-123")
	}
	if backend.lastEnqueuedRun.Prompt != "follow up" {
		t.Fatalf("queued prompt = %q, want %q", backend.lastEnqueuedRun.Prompt, "follow up")
	}
	if backend.lastEnqueuedRun.Mode != "later" {
		t.Fatalf("queued mode = %q, want %q", backend.lastEnqueuedRun.Mode, "later")
	}
	if got := m.textInput.Value(); got != "" {
		t.Fatalf("textInput.Value() after queue = %q, want empty", got)
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"At end of turn", "1 queued", "↳ follow up"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("queued follow-up preview missing %q in %q", want, rendered)
		}
	}
}

func TestEnterWhileStreamingQueuesSteer(t *testing.T) {
	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.streaming = true
	m.sessionID = "session-123"
	m.textInput.SetValue("be more concise")
	m.textInput.Focus()

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("enter while streaming should queue a steer")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastEnqueuedRun.Mode != "next" {
		t.Fatalf("queued mode = %q, want %q", backend.lastEnqueuedRun.Mode, "next")
	}
	if backend.lastEnqueuedRun.Prompt != "be more concise" {
		t.Fatalf("queued prompt = %q, want %q", backend.lastEnqueuedRun.Prompt, "be more concise")
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"After next tool call", "1 queued", "↳ be more concise"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("queued steer preview missing %q in %q", want, rendered)
		}
	}
}

func testModelCatalog() *rpc.ModelCatalogResponse {
	return &rpc.ModelCatalogResponse{
		Providers: []rpc.ModelCatalogProvider{
			{
				Provider:       "ollama",
				DefaultModelID: "ollama:kimi-k2:1t-cloud",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "ollama:kimi-k2:1t-cloud", Description: "Current default Kimi K2"},
					{ModelID: "ollama:glm-5:cloud", Description: "GLM-5 cloud path"},
					{ModelID: "ollama:qwen3.5:397b-cloud", Description: "Qwen 3.5 397B cloud"},
					{ModelID: "ollama:qwen3-coder-next", Description: "Qwen3 Coder Next"},
				},
			},
			{
				Provider:       "openai",
				DefaultModelID: "openai-responses:gpt-5.4",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "openai-responses:gpt-5.4", Description: "Default GPT-5.4 Responses path"},
					{ModelID: "openai-responses:gpt-5.4-mini", Description: "Faster GPT-5.4 mini Responses path"},
					{ModelID: "openai-responses:gpt-5.3-codex", Description: "Codex-optimized GPT-5.3 Responses path"},
				},
			},
			{
				Provider:       "openrouter",
				DefaultModelID: "openrouter:anthropic/claude-sonnet-4-5",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "openrouter:anthropic/claude-sonnet-4-5", Description: "OpenRouter Claude Sonnet"},
				},
			},
			{
				Provider:       "anthropic",
				DefaultModelID: "anthropic:claude-sonnet-4-5",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "anthropic:claude-sonnet-4-5", Description: "Balanced Claude Sonnet"},
					{ModelID: "anthropic:claude-opus-4-1", Description: "Stronger Claude Opus"},
				},
			},
			{
				Provider:       "google",
				DefaultModelID: "google:gemini-2.5-flash",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "google:gemini-2.5-flash", Description: "Fast Gemini 2.5 Flash"},
					{ModelID: "google:gemini-2.5-flash-lite", Description: "Cheaper Gemini 2.5 Flash-Lite"},
					{ModelID: "google:gemini-2.5-pro", Description: "Stronger Gemini 2.5 Pro"},
				},
			},
		},
	}
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

func TestCurrentViewModelMarksDetachedLiveWhenStreamingScrolledUp(t *testing.T) {
	m := newTestModel()
	m.width = 80
	m.height = 24
	for i := 0; i < 30; i++ {
		m.transcript.WriteLine(fmt.Sprintf("line %02d", i))
	}
	m.streaming = true
	m.phase = PhaseStreaming
	m.runStartTime = time.Now().Add(-37 * time.Second)
	m.refreshViewport()
	m.viewport.GotoTop()

	vm := m.currentViewModel()

	if !vm.DetachedLive {
		t.Fatal("currentViewModel() should mark detached live state when scrolled off bottom during streaming")
	}
}

func TestNewResumedSessionSkipsFirstRunOnboardingAndShowsResumeNote(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := New(Options{
		Model:         "openai-responses:gpt-5.4",
		WorkspaceRoot: "/workspace",
		SessionID:     "0123456789abcdef0123456789abcdef",
		SessionName:   "auth-store-cleanup",
	}).(*model)

	if m.onboarding.Active {
		t.Fatal("resumed session should not show first-run onboarding")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "resumed auth-store-cleanup") {
		t.Fatalf("startup transcript missing resumed session note: %q", rendered)
	}
}

func TestResumedSessionPreviewHydratesRecentHistory(t *testing.T) {
	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.sessionID = "0123456789abcdef0123456789abcdef"
	m.sessionName = "auth-store-cleanup"
	m.transcript.WriteNote("session", []string{"resumed auth-store-cleanup"})

	updated, _ := m.Update(sessionPreviewLoadedMsg{
		Preview: rpc.SessionPreviewResponse{
			SessionID: "0123456789abcdef0123456789abcdef",
			Entries: []rpc.SessionPreviewEntry{
				{Kind: "user", Text: "fix the auth store"},
				{Kind: "assistant", Text: "I updated the auth store logic."},
			},
			Truncated: true,
		},
	})

	rendered := stripANSI(updated.(*model).transcript.Render())
	if !strings.Contains(rendered, "showing recent session history") {
		t.Fatalf("resume preview note missing from transcript: %q", rendered)
	}
	if !strings.Contains(rendered, "older history omitted") {
		t.Fatalf("resume preview truncation note missing from transcript: %q", rendered)
	}
	if !strings.Contains(rendered, "> fix the auth store") {
		t.Fatalf("resumed user turn missing from transcript: %q", rendered)
	}
	if !strings.Contains(rendered, "I updated the auth store logic.") {
		t.Fatalf("resumed assistant turn missing from transcript: %q", rendered)
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

func TestRefreshViewportMeasuresPromptHeightFromRenderedLayout(t *testing.T) {
	m := newTestModel()

	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	m = updated.(*model)
	baseHeight := m.viewport.Height

	m = sendRunes(m, "/")
	if m.viewport.Height >= baseHeight {
		t.Fatalf("viewport height = %d, want smaller than idle height %d when slash menu opens", m.viewport.Height, baseHeight)
	}

	want := max(1, m.height-promptHeight(m.currentViewModel()))
	if m.viewport.Height != want {
		t.Fatalf("viewport height = %d, want %d from measured prompt height", m.viewport.Height, want)
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEsc})
	if m.viewport.Height != baseHeight {
		t.Fatalf("viewport height after closing slash menu = %d, want %d", m.viewport.Height, baseHeight)
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
	if !strings.Contains(rendered, "Conversation interrupted.") {
		t.Fatalf("view missing interrupt guidance: %q", rendered)
	}

	m.Update(runEventMsg{Event: rpc.RunEvent{Type: "assistant_text_delta", Delta: "still running"}})
	m.Update(liveFlushMsg{})
	rendered = stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "still running") {
		t.Fatalf("streaming output was dropped after ctrl+c: %q", rendered)
	}
}

func TestUpdateCheckMsgShowsInstalledUpdatePrompt(t *testing.T) {
	m := newTestModel()
	m.appVersion = "0.1.0"

	updated, _ := m.Update(updateCheckMsg{
		LatestVersion: "0.1.1",
		Command:       []string{"uv", "tool", "upgrade", "just-another-coding-agent"},
	})
	m = updated.(*model)

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"update available  0.1.0 -> 0.1.1",
		"runs: uv tool upgrade just-another-coding-agent",
		"Update now",
		"Skip until 0.1.1",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("update prompt missing %q in %q", want, rendered)
		}
	}
}

func TestUpdatePromptSkipUntilNextReleasePersistsChoice(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.updatePrompt = updatePromptState{
		Active:         true,
		CurrentVersion: "0.1.0",
		LatestVersion:  "0.1.1",
		Command:        []string{"uv", "tool", "upgrade", "just-another-coding-agent"},
		Selected:       2,
	}

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)

	if m.updatePrompt.Active {
		t.Fatal("expected skip-until selection to close update prompt")
	}
	if m.skippedUpdateVersion != "0.1.1" {
		t.Fatalf("skippedUpdateVersion = %q, want %q", m.skippedUpdateVersion, "0.1.1")
	}

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("reading config.json: %v", err)
	}
	if !strings.Contains(string(data), `"update_skip_version": "0.1.1"`) {
		t.Fatalf("config.json missing update skip version: %q", string(data))
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
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.streaming = true
	m.phase = PhaseStreaming
	m.sessionID = "session-123"

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		t.Fatal("expected interrupt command")
	}
	m = updated.(*model)

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Conversation interrupted.") {
		t.Fatalf("missing interrupt guidance in prompt footer: %q", rendered)
	}
	msg := cmd()
	done, ok := msg.(interruptRunDoneMsg)
	if !ok {
		t.Fatalf("interrupt command returned %T", msg)
	}
	if done.Err != nil {
		t.Fatalf("interrupt command error: %v", done.Err)
	}
	if backend.lastInterruptedRun.SessionID != "session-123" {
		t.Fatalf(
			"interrupted session id = %q, want %q",
			backend.lastInterruptedRun.SessionID,
			"session-123",
		)
	}
	if !backend.lastInterruptedRun.PromoteQueuedSteer {
		t.Fatal("expected escape interrupt to promote queued steer")
	}
}

func TestSecondEscDoesNotRestorePreviousPromptIntoComposer(t *testing.T) {
	m := newTestModel()
	m.promptHistory = []string{"first prompt", "previous prompt"}
	m.historyIndex = -1
	m.textInput.SetValue("")
	m.streaming = true
	m.phase = PhaseStreaming

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if !strings.Contains(stripANSI(m.View()), "Conversation interrupted.") {
		t.Fatalf("first escape did not render interrupt notice: %q", stripANSI(m.View()))
	}

	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "" {
		t.Fatalf("textInput.Value() = %q, want empty", got)
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
	if !strings.Contains(rendered, "Conversation interrupted.") {
		t.Fatalf("missing interrupt guidance in prompt footer: %q", rendered)
	}
}

func TestRunSucceededUsageAppearsInFooter(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.phase = PhaseStreaming

	m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:                   "run_succeeded",
		RunID:                  "run-1",
		OutputText:             "done",
		InputTokens:            intPtr(120),
		OutputTokens:           intPtr(45),
		TotalTokens:            intPtr(165),
		ContextWindowUsed:      floatPtr(0.413),
		NextRequestContextUsed: floatPtr(0.07),
	}})
	m.Update(runEventMsg{Done: true})

	rendered := stripANSI(m.View())
	for _, want := range []string{"completed", "93% left"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing %q in %q", want, rendered)
		}
	}
	for _, unwanted := range []string{"120 in", "45 out", "165 tok", "41% ctx"} {
		if strings.Contains(rendered, unwanted) {
			t.Fatalf("view unexpectedly includes %q in %q", unwanted, rendered)
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

	updated, _ = m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:            "session_compaction_warning",
		CompactionCount: intPtr(2),
		Message:         "Session has been compacted multiple times; continuity quality may degrade.",
	}})
	m = updated.(*model)

	if m.phase != PhaseStreaming {
		t.Fatalf("phase after compaction warning = %q, want %q", m.phase, PhaseStreaming)
	}

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"compacting session...",
		"session compacted",
		"Session has been compacted multiple times; continuity quality may degrade.",
	} {
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
		"google",
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

func TestAuthSlashSuggestionsIncludeOllama(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/auth ")

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "ollama") {
		t.Fatalf("view missing ollama auth suggestion in %q", rendered)
	}
	if !strings.Contains(rendered, "google") {
		t.Fatalf("view missing google auth suggestion in %q", rendered)
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
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model openai-responses:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"default_model": "openai-responses:gpt-5.4"`) {
		t.Fatalf("config.json missing persisted model: %q", string(data))
	}
	if !strings.Contains(string(data), `"default_provider": "openai"`) {
		t.Fatalf("config.json missing persisted provider: %q", string(data))
	}
}

func TestModelCatalogDeadlineExceededDoesNotWriteStartupError(t *testing.T) {
	m := newTestModel()
	m.modelCatalog = nil
	m.modelCatalogLoading = true

	updated, _ := m.Update(modelCatalogLoadedMsg{Err: context.DeadlineExceeded})
	m = updated.(*model)

	if m.modelCatalogLoading {
		t.Fatal("modelCatalogLoading should clear after catalog load result")
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "model catalog:") {
		t.Fatalf("transcript should not surface startup catalog timeout: %q", got)
	}
}

func TestStartupAuthStatusShowsFirstRunOnboarding(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"Get Started",
		"1. Ollama",
		"2. OpenAI",
		"3. Anthropic",
		"4. Google Gemini",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("startup onboarding missing %q in %q", want, rendered)
		}
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "first-time setup") {
		t.Fatalf("first-run chooser should not write transcript note: %q", got)
	}
	if !m.onboarding.Active {
		t.Fatal("first-run onboarding should open chooser panel")
	}
	if m.auth.Active {
		t.Fatal("first-run onboarding should not auto-start auth")
	}
	if got := m.currentPromptFooter(); got != "" {
		t.Fatalf("currentPromptFooter() = %q, want empty while chooser is active", got)
	}
}

func TestNewWithFreshHomeStartsChooserImmediately(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := New(Options{
		Model:         "ollama:test",
		WorkspaceRoot: "/workspace",
		Thinking:      "medium",
	}).(*model)

	if !m.onboarding.Active || m.onboarding.Kind != "provider" {
		t.Fatalf("onboarding state = %#v, want active provider chooser", m.onboarding)
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"Get Started", "1. Ollama", "2. OpenAI", "4. Google Gemini"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("initial chooser missing %q in %q", want, rendered)
		}
	}
}

func TestFirstRunChooserDoesNotDependOnAuthStatus(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.maybeStartOnboarding()

	if !m.onboarding.Active {
		t.Fatal("first-run chooser should open without auth status")
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{
		"Get Started",
		"1. Ollama",
		"2. OpenAI",
		"3. Anthropic",
		"4. Google Gemini",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("first-run chooser missing %q in %q", want, rendered)
		}
	}
}

func TestEscapeFromFirstRunAuthReturnsToProviderChooser(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	m = sendKey(m, tea.KeyMsg{Runes: []rune("2"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	if !m.auth.Active {
		t.Fatal("openai selection should start auth")
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEsc})

	if m.auth.Active {
		t.Fatal("esc should close auth panel")
	}
	if !m.onboarding.Active || m.onboarding.Kind != "provider" {
		t.Fatalf("onboarding state = %#v, want provider chooser", m.onboarding)
	}
	if m.onboarding.Selected != 1 {
		t.Fatalf("onboarding.Selected = %d, want 1 for openai", m.onboarding.Selected)
	}
	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Get Started") || !strings.Contains(rendered, "2. OpenAI") {
		t.Fatalf("provider chooser missing after esc: %q", rendered)
	}
}

func TestFirstRunEscapeThenTabOpensProviderSuggestions(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEsc})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyTab})

	if got := m.textInput.Value(); got != "/provider " {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/provider ")
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"ollama", "openai", "anthropic", "google"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("provider suggestion %q missing in %q", want, rendered)
		}
	}
}

func TestFirstRunChoosingOpenAIOpensSecureSetupPanel(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	m = sendKey(m, tea.KeyMsg{Runes: []rune("2"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Secure Setup") || !strings.Contains(rendered, "OpenAI API key") {
		t.Fatalf("view missing openai secure setup panel after chooser selection: %q", rendered)
	}
	if m.onboarding.Active {
		t.Fatal("onboarding chooser should close after provider selection")
	}
	if !m.auth.Active || m.auth.Provider != "openai" {
		t.Fatalf("auth state = %#v, want active openai auth", m.auth)
	}
}

func TestFirstRunChoosingConfiguredAnthropicSkipsAuth(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	backend.authStatuses["anthropic"] = rpc.AuthProviderStatus{
		Provider:   "anthropic",
		Configured: true,
		Source:     "file",
		EnvKey:     "ANTHROPIC_API_KEY",
	}
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	m = sendKey(m, tea.KeyMsg{Runes: []rune("3"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if m.auth.Active {
		t.Fatal("configured anthropic provider should not reopen auth")
	}
	if m.onboarding.Active {
		t.Fatal("onboarding chooser should close after configured provider selection")
	}
	if got := m.options.Model; got != "anthropic:claude-sonnet-4-5" {
		t.Fatalf("options.Model = %q, want %q", got, "anthropic:claude-sonnet-4-5")
	}
	configText, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "provider set to anthropic") {
		t.Fatalf("transcript missing anthropic provider selection: %q", rendered)
	}
	if !strings.Contains(string(configText), `"default_provider": "anthropic"`) {
		t.Fatalf("config.json missing anthropic provider selection: %q", string(configText))
	}
}

func TestFirstRunChoosingConfiguredHostedOllamaSkipsAuth(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	backend.authStatuses["ollama"] = rpc.AuthProviderStatus{
		Provider:   "ollama",
		Configured: true,
		Source:     "file",
		EnvKey:     "OLLAMA_API_KEY",
	}
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	m = sendKey(m, tea.KeyMsg{Runes: []rune("1"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendKey(m, tea.KeyMsg{Runes: []rune("2"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if m.auth.Active {
		t.Fatal("configured hosted ollama should not reopen auth")
	}
	if m.onboarding.Active {
		t.Fatal("ollama mode chooser should close after configured hosted selection")
	}
	configText, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(configText), `"default_provider": "ollama"`) {
		t.Fatalf("config.json missing ollama provider selection: %q", string(configText))
	}
	if !strings.Contains(string(configText), `"OLLAMA_BASE_URL": "https://ollama.com/v1"`) {
		t.Fatalf("config.json missing hosted ollama base URL: %q", string(configText))
	}
}

func TestFirstRunChoosingOllamaShowsModeChooser(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	m = sendKey(m, tea.KeyMsg{Runes: []rune("1"), Type: tea.KeyRunes})
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	for _, want := range []string{"Choose Ollama Mode", "1. Local Ollama", "2. Hosted Ollama"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("ollama mode chooser missing %q in %q", want, rendered)
		}
	}
	if !m.onboarding.Active || m.onboarding.Kind != "ollama" {
		t.Fatalf("onboarding state = %#v, want active ollama chooser", m.onboarding)
	}
}

func TestChoosingLocalOllamaClearsHostedEndpointImmediately(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := config.SaveOllamaBaseURL(config.OllamaCloudBaseURL); err != nil {
		t.Fatalf("SaveOllamaBaseURL() returned error: %v", err)
	}

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.options.Model = "ollama:gemma4:e4b"
	m.onboarding = onboardingState{Active: true, Kind: "ollama", Selected: 0}

	updated, _ := m.completeOnboardingSelection()
	m = updated.(*model)

	got, err := config.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if _, ok := got["OLLAMA_BASE_URL"]; ok {
		t.Fatalf("local ollama selection should clear hosted base URL: %v", got)
	}
	if backend.restarts != 1 {
		t.Fatalf("backend.restarts = %d, want %d", backend.restarts, 1)
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "Ollama cloud endpoint cleared.") {
		t.Fatalf("transcript missing local endpoint note: %q", rendered)
	}
}

func TestStartupAuthStatusAutoStartsAuthForPersistedProviderWithoutCredentials(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	if err := config.SaveDefaultProvider("openai"); err != nil {
		t.Fatalf("SaveDefaultProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("openai-responses:gpt-5.4"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Model = "openai-responses:gpt-5.4"
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	if !m.auth.Active {
		t.Fatal("startup auth should start masked auth flow for missing openai credentials")
	}
	if m.auth.Provider != "openai" {
		t.Fatalf("auth.Provider = %q, want %q", m.auth.Provider, "openai")
	}
	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "note  provider setup") {
		t.Fatalf("startup should not write provider setup note: %q", rendered)
	}
	view := stripANSI(m.View())
	if !strings.Contains(view, "Secure Setup") || !strings.Contains(view, "OpenAI API key") {
		t.Fatalf("view missing openai secure setup panel: %q", view)
	}
}

func TestStartupAuthStatusTimeoutSchedulesRetry(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	if err := config.SaveDefaultProvider("openai"); err != nil {
		t.Fatalf("SaveDefaultProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("openai-responses:gpt-5.4"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Model = "openai-responses:gpt-5.4"

	updated, cmd := m.Update(authStatusLoadedMsg{Err: context.DeadlineExceeded})
	m = updated.(*model)

	if m.auth.Active {
		t.Fatal("startup auth should not enter auth flow before retry completes")
	}
	if cmd == nil {
		t.Fatal("auth status timeout should schedule a retry")
	}
	msg := cmd()
	if _, ok := msg.(authStatusRetryMsg); !ok {
		t.Fatalf("retry cmd returned %T, want authStatusRetryMsg", msg)
	}
}

func TestStartupAuthStatusAutoStartsAuthForPersistedHostedOllamaSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "")

	if err := config.SaveProvider(config.ProviderUpdate{
		Provider: "ollama",
		BaseURL:  config.OllamaCloudBaseURL,
	}); err != nil {
		t.Fatalf("SaveProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("ollama:kimi-k2:1t-cloud"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Model = "ollama:kimi-k2:1t-cloud"
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	if !m.auth.Active {
		t.Fatal("startup auth should start masked auth flow for hosted ollama selection")
	}
	if m.auth.Provider != "ollama" {
		t.Fatalf("auth.Provider = %q, want %q", m.auth.Provider, "ollama")
	}
	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "the shipped Ollama provider path uses hosted Ollama models") {
		t.Fatalf("startup should not write hosted ollama setup note: %q", rendered)
	}
	view := stripANSI(m.View())
	if !strings.Contains(view, "Secure Setup") || !strings.Contains(view, "Ollama cloud API key") {
		t.Fatalf("view missing ollama secure setup panel: %q", view)
	}
}

func TestStartupAuthStatusDoesNotStartAuthForPersistedLocalOllamaSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "")

	if err := config.SaveProvider(config.ProviderUpdate{Provider: "ollama"}); err != nil {
		t.Fatalf("SaveProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("ollama:llama3.2"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Model = "ollama:llama3.2"
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	if m.auth.Active {
		t.Fatalf("local ollama startup should not start auth: %#v", m.auth)
	}
}

func TestModelCommandRequestsCatalogWhenMissing(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")

	m := newTestModel()
	m.modelCatalog = nil
	m.options.Backend = newStubBackend()

	updated, cmd := m.handleModelCommand("openai-responses:gpt-5.4")
	m = updated.(*model)

	if cmd == nil {
		t.Fatal("handleModelCommand should request model catalog when missing")
	}
	if !m.modelCatalogLoading {
		t.Fatal("model catalog load should be marked in flight")
	}
	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4")
	}
}

func TestTraceCommandPersistsMode(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()

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
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Secure Setup") || !strings.Contains(rendered, "OpenAI API key") {
		t.Fatalf("view missing secure setup panel after provider selection: %q", rendered)
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

func TestOllamaProviderCommandOpensModeChooser(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/provider ollama")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	for _, want := range []string{"Choose Ollama Mode", "1. Local Ollama", "2. Hosted Ollama"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing %q after /provider ollama: %q", want, rendered)
		}
	}
	if m.auth.Active {
		t.Fatalf("ollama provider command should not jump straight to auth: %#v", m.auth)
	}
}

func TestProviderWithoutCredentialsShowsSecureSetupPanel(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"Secure Setup",
		"OpenAI API key",
		"Enter your OpenAI API key",
		"Stored in the OS keychain",
		"Not added to transcript or prompt history",
		"Enter saves. Esc cancels.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("secure setup panel missing %q in %q", want, rendered)
		}
	}
	if transcript := stripANSI(m.transcript.Render()); strings.Contains(transcript, "note  secure setup") {
		t.Fatalf("secure setup panel should not write transcript note: %q", transcript)
	}
}

func TestProviderWithoutKeychainStartsLocalFileAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	message := "No supported OS keychain backend is available for local provider secret storage."
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     false,
		Message:       &message,
		FileStorePath: filepath.Join(home, ".jaca", "secrets.json"),
	}
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Local Secret File") {
		t.Fatalf("view missing local-secret-file panel: %q", rendered)
	}
	if !strings.Contains(rendered, "OS keychain unavailable; using local secret file instead") {
		t.Fatalf("view missing automatic file-store reason: %q", rendered)
	}
	if !strings.Contains(rendered, "secrets.json") {
		t.Fatalf("view missing file-store path: %q", rendered)
	}
	if !m.auth.Active {
		t.Fatal("file auth should be active")
	}
	if got := m.auth.Storage; got != "file" {
		t.Fatalf("auth.Storage = %q, want %q", got, "file")
	}
}

func TestAuthAnthropicWithoutKeychainUsesLocalFileFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("ANTHROPIC_API_KEY", "")

	backend := newStubBackend()
	message := "No supported OS keychain backend is available for local provider secret storage."
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     false,
		Message:       &message,
		FileStorePath: filepath.Join(home, ".jaca", "secrets.json"),
	}
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/auth anthropic")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Local Secret File") {
		t.Fatalf("view missing local-secret-file panel: %q", rendered)
	}
	if !strings.Contains(rendered, "Anthropic API key") {
		t.Fatalf("view missing anthropic secret prompt: %q", rendered)
	}
	if !m.auth.Active {
		t.Fatal("anthropic file auth should be active")
	}
	if got := m.auth.Storage; got != "file" {
		t.Fatalf("auth.Storage = %q, want %q", got, "file")
	}
}

func TestAuthSaveFailsIfProviderStatusStillUnconfiguredAfterSave(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("ANTHROPIC_API_KEY", "")

	backend := newStubBackend()
	message := "No supported OS keychain backend is available for local provider secret storage."
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     false,
		Message:       &message,
		FileStorePath: filepath.Join(home, ".jaca", "secrets.json"),
	}
	backend.authStatusAfterSet["anthropic"] = rpc.AuthProviderStatus{
		Provider:   "anthropic",
		Configured: false,
		Source:     "none",
		EnvKey:     "ANTHROPIC_API_KEY",
	}
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/auth anthropic")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "sk-ant-test")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if m.auth.Active {
		t.Fatal("auth flow should close after failed persistence verification")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "Anthropic secret did not persist") {
		t.Fatalf("transcript missing persistence failure message: %q", rendered)
	}
}

func TestPromptWithMissingGoogleCredentialsStartsAuthInsteadOfRun(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("GOOGLE_API_KEY", "")

	if err := config.SaveDefaultProvider("google"); err != nil {
		t.Fatalf("SaveDefaultProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("google:gemini-2.5-flash"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	backend := newStubBackend()
	message := "No supported OS keychain backend is available for local provider secret storage."
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     false,
		Message:       &message,
		FileStorePath: filepath.Join(home, ".jaca", "secrets.json"),
	}
	m := newTestModel()
	m.options.Backend = backend
	m.options.Model = "google:gemini-2.5-flash"

	m = sendRunes(m, "hello")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)

	if !m.auth.Active {
		t.Fatal("missing google credentials should open auth instead of starting a run")
	}
	if m.streaming {
		t.Fatal("model run should not start when provider credentials are missing")
	}
}

func TestPromptRefreshesAuthStatusBeforeStartingRun(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("GOOGLE_API_KEY", "")

	if err := config.SaveDefaultProvider("google"); err != nil {
		t.Fatalf("SaveDefaultProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("google:gemini-2.5-flash"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	backend := newStubBackend()
	message := "No supported OS keychain backend is available for local provider secret storage."
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     false,
		Message:       &message,
		FileStorePath: filepath.Join(home, ".jaca", "secrets.json"),
	}
	backend.authStatuses["google"] = rpc.AuthProviderStatus{
		Provider:   "google",
		Configured: true,
		Source:     "file",
		EnvKey:     "GOOGLE_API_KEY",
	}
	m := newTestModel()
	m.options.Backend = backend
	m.options.Model = "google:gemini-2.5-flash"
	m.authStatus = &rpc.AuthStatusResponse{
		Providers: []rpc.AuthProviderStatus{
			{
				Provider:   "google",
				Configured: true,
				Source:     "file",
				EnvKey:     "GOOGLE_API_KEY",
			},
		},
		LocalSecretStore: backend.localSecretStore,
	}
	delete(backend.authStatuses, "google")

	m = sendRunes(m, "hello")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)

	if !m.auth.Active {
		t.Fatal("fresh auth status should reopen auth when credentials disappeared")
	}
	if m.streaming {
		t.Fatal("model run should not start when refreshed auth status reports missing credentials")
	}
}

func TestGoogleProviderWithoutCredentialsStartsMaskedAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("GOOGLE_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/provider google")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Secure Setup") || !strings.Contains(rendered, "Google API key") {
		t.Fatalf("view missing google secure setup panel after provider selection: %q", rendered)
	}
	masked := sendRunes(m, "super-secret")
	rendered = stripANSI(masked.View())
	if strings.Contains(rendered, "super-secret") {
		t.Fatalf("secret leaked into rendered view: %q", rendered)
	}
	if got := masked.promptHistory; len(got) != 1 || got[0] != "/provider google" {
		t.Fatalf("promptHistory = %#v, want only the non-secret provider command", got)
	}
}

func TestAuthOllamaUsesCloudSpecificSecretLabel(t *testing.T) {
	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/auth ollama")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Ollama cloud API key") {
		t.Fatalf("secure setup panel missing ollama cloud label: %q", rendered)
	}
}

func TestGoogleAuthSubmissionAppliesPendingModelSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("GOOGLE_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model google:gemini-2.5-flash")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "google-token")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "google:gemini-2.5-flash" {
		t.Fatalf("options.Model = %q, want %q", got, "google:gemini-2.5-flash")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	configText := string(data)
	if !strings.Contains(configText, `"default_provider": "google"`) {
		t.Fatalf("config.json missing provider selection: %q", configText)
	}
	if !strings.Contains(configText, `"default_model": "google:gemini-2.5-flash"`) {
		t.Fatalf("config.json missing model selection: %q", configText)
	}
	if strings.Contains(configText, `"GOOGLE_API_KEY"`) {
		t.Fatalf("config.json should not store google credential: %q", configText)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "google-token") {
		t.Fatalf("secret leaked into transcript: %q", stripANSI(m.transcript.Render()))
	}
	if backend.lastSetSecret.Provider != "google" || backend.lastSetSecret.Secret != "google-token" || backend.lastSetSecret.Storage != "keychain" {
		t.Fatalf("backend lastSetSecret = %#v", backend.lastSetSecret)
	}
}

func TestPromptRequiringAuthIsRestoredAfterSuccessfulSubmission(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.options.Model = "openai-responses:gpt-5.4"

	m = sendRunes(m, "run go tests")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if !m.auth.Active {
		t.Fatal("missing credentials should open auth")
	}

	m = sendRunes(m, "openai-token")
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "run go tests" {
		t.Fatalf("textInput.Value() = %q, want original prompt restored", got)
	}
	if m.auth.Active {
		t.Fatal("auth overlay should close after successful auth")
	}
	if m.streaming {
		t.Fatal("successful auth should restore the prompt, not start the run automatically")
	}
}

func TestPromptRequiringAuthIsRestoredOnEscape(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.options.Model = "openai-responses:gpt-5.4"

	m = sendRunes(m, "run go tests")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if !m.auth.Active {
		t.Fatal("missing credentials should open auth")
	}

	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(*model)

	if got := m.textInput.Value(); got != "run go tests" {
		t.Fatalf("textInput.Value() = %q, want original prompt restored", got)
	}
	if m.auth.Active {
		t.Fatal("auth overlay should close after escape")
	}
}

func TestModelWithoutCredentialsStartsMaskedAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model openai-responses:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Secure Setup") || !strings.Contains(rendered, "OpenAI API key") {
		t.Fatalf("view missing secure setup panel after model selection: %q", rendered)
	}
	if got := m.promptHistory; len(got) != 1 || got[0] != "/model openai-responses:gpt-5.4" {
		t.Fatalf("promptHistory = %#v, want only the non-secret model command", got)
	}
}

func TestLocalOllamaModelDoesNotStartAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model ollama:llama3.2")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if m.auth.Active {
		t.Fatal("local ollama model selection should not start auth")
	}
	if got := m.options.Model; got != "ollama:llama3.2" {
		t.Fatalf("options.Model = %q, want %q", got, "ollama:llama3.2")
	}
}

func TestHostedOllamaModelStartsMaskedAuthFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model ollama:kimi-k2:1t-cloud")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Secure Setup") || !strings.Contains(rendered, "Ollama cloud API key") {
		t.Fatalf("view missing hosted Ollama secure setup panel after model selection: %q", rendered)
	}
}

func TestLocalOllamaModelClearsHostedBaseURL(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OLLAMA_API_KEY", "test-key")

	if err := config.SaveProvider(config.ProviderUpdate{
		Provider: "ollama",
		BaseURL:  config.OllamaCloudBaseURL,
	}); err != nil {
		t.Fatalf("SaveProvider() returned error: %v", err)
	}
	if err := config.SaveDefaultModel("ollama:kimi-k2:1t-cloud"); err != nil {
		t.Fatalf("SaveDefaultModel() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Model = "ollama:kimi-k2:1t-cloud"
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model ollama:llama3.2")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	got, err := config.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if _, ok := got["OLLAMA_BASE_URL"]; ok {
		t.Fatalf("local ollama model selection should clear hosted base URL: %v", got)
	}
	if got["default_model"] != "ollama:llama3.2" {
		t.Fatalf("default_model = %q, want %q", got["default_model"], "ollama:llama3.2")
	}
}

func TestAuthStatusCommandRendersProviderSources(t *testing.T) {
	backend := newStubBackend()
	backend.authStatuses["google"] = rpc.AuthProviderStatus{
		Provider:   "google",
		Configured: true,
		Source:     "keychain",
		EnvKey:     "GOOGLE_API_KEY",
	}

	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/auth status")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "google: configured (keychain)") {
		t.Fatalf("transcript missing google auth status: %q", rendered)
	}
}

func TestAuthClearCommandCallsBackend(t *testing.T) {
	backend := newStubBackend()
	backend.authStatuses["openai"] = rpc.AuthProviderStatus{
		Provider:   "openai",
		Configured: true,
		Source:     "keychain",
		EnvKey:     "OPENAI_API_KEY",
	}

	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/auth clear openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if backend.lastCleared != "openai" {
		t.Fatalf("backend.lastCleared = %q, want %q", backend.lastCleared, "openai")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "openai auth cleared; current source: missing (none)") {
		t.Fatalf("transcript missing clear confirmation: %q", rendered)
	}
}

func TestNameCommandCallsBackendAndShowsNormalizedName(t *testing.T) {
	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.sessionID = "0123456789abcdef0123456789abcdef"

	m = sendRunes(m, "/name Auth Store Cleanup")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if backend.lastNamedSession != m.sessionID {
		t.Fatalf("backend.lastNamedSession = %q, want %q", backend.lastNamedSession, m.sessionID)
	}
	if backend.lastSessionName != "Auth Store Cleanup" {
		t.Fatalf("backend.lastSessionName = %q, want %q", backend.lastSessionName, "Auth Store Cleanup")
	}
	if got := m.sessionName; got != "auth-store-cleanup" {
		t.Fatalf("sessionName = %q, want %q", got, "auth-store-cleanup")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "session named auth-store-cleanup") {
		t.Fatalf("transcript missing session naming confirmation: %q", rendered)
	}
}

func TestSessionCommandShowsSessionNameAndID(t *testing.T) {
	m := newTestModel()
	m.sessionID = "0123456789abcdef0123456789abcdef"
	m.sessionName = "auth-store-cleanup"

	m = sendRunes(m, "/session")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "session: auth-store-cleanup") {
		t.Fatalf("transcript missing session name: %q", rendered)
	}
	if !strings.Contains(rendered, "id: 0123456789abcdef0123456789abcdef") {
		t.Fatalf("transcript missing session id: %q", rendered)
	}
}

func TestSessionCommandShowsForkLineage(t *testing.T) {
	m := newTestModel()
	m.sessionID = "fedcba9876543210fedcba9876543210"
	m.sessionName = "auth-store-cleanup-followup"
	m.forkedFromSessionID = "0123456789abcdef0123456789abcdef"
	m.forkedFromSessionName = "auth-store-cleanup"

	m = sendRunes(m, "/session")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "forked from: auth-store-cleanup") {
		t.Fatalf("transcript missing fork lineage: %q", rendered)
	}
}

func TestNewForkedSessionShowsForkNote(t *testing.T) {
	teaModel := New(Options{
		AppVersion:            "0.1.4",
		Model:                 "ollama:kimi-k2:1t-cloud",
		WorkspaceRoot:         "/workspace",
		SessionsRoot:          "/sessions",
		SessionID:             "fedcba9876543210fedcba9876543210",
		SessionName:           "auth-store-cleanup-followup",
		ForkedFromSessionID:   "0123456789abcdef0123456789abcdef",
		ForkedFromSessionName: "auth-store-cleanup",
	})

	m := teaModel.(*model)
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "forked from auth-store-cleanup") {
		t.Fatalf("startup transcript missing fork note: %q", rendered)
	}
}

func TestNameCommandWithoutActiveSessionFailsHard(t *testing.T) {
	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/name auth store cleanup")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "no active session") {
		t.Fatalf("transcript missing no-active-session error: %q", rendered)
	}
}

func TestAuthSubmissionStoresCredentialWithoutLeakingSecret(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/provider openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "super-secret")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	configText := string(data)
	if strings.Contains(configText, `"OPENAI_API_KEY"`) {
		t.Fatalf("config.json should not store openai credential: %q", configText)
	}
	if !strings.Contains(configText, `"default_provider": "openai"`) {
		t.Fatalf("config.json missing provider selection: %q", configText)
	}
	if !strings.Contains(configText, `"default_model": "openai-responses:gpt-5.4"`) {
		t.Fatalf("config.json missing default model selection: %q", configText)
	}
	transcript := stripANSI(m.transcript.Render())
	if strings.Contains(transcript, "super-secret") {
		t.Fatalf("secret leaked into transcript: %q", transcript)
	}
	if len(m.promptHistory) != 1 || m.promptHistory[0] != "/provider openai" {
		t.Fatalf("promptHistory = %#v, want only the provider command", m.promptHistory)
	}
	if backend.lastSetSecret.Provider != "openai" || backend.lastSetSecret.Secret != "super-secret" || backend.lastSetSecret.Storage != "keychain" {
		t.Fatalf("backend lastSetSecret = %#v", backend.lastSetSecret)
	}
}

func TestAuthSubmissionAppliesPendingModelSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model openai-responses:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})
	m = sendRunes(m, "super-secret")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	configText := string(data)
	if !strings.Contains(configText, `"default_provider": "openai"`) {
		t.Fatalf("config.json missing provider selection: %q", configText)
	}
	if !strings.Contains(configText, `"default_model": "openai-responses:gpt-5.4"`) {
		t.Fatalf("config.json missing model selection: %q", configText)
	}
	if strings.Contains(configText, `"OPENAI_API_KEY"`) {
		t.Fatalf("config.json should not store openai credential: %q", configText)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "super-secret") {
		t.Fatalf("secret leaked into transcript: %q", stripANSI(m.transcript.Render()))
	}
	if backend.lastSetSecret.Provider != "openai" || backend.lastSetSecret.Secret != "super-secret" || backend.lastSetSecret.Storage != "keychain" {
		t.Fatalf("backend lastSetSecret = %#v", backend.lastSetSecret)
	}
}
