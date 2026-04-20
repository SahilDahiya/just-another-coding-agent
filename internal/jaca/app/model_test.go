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
	model                string
	modelCatalog         rpc.ModelCatalogResponse
	modelCatalogErr      error
	authStatuses         map[string]rpc.AuthProviderStatus
	oauthStatuses        map[string]rpc.OAuthProviderStatus
	logfireStatus        rpc.TraceLogfireStatusResponse
	permissionState      rpc.PermissionState
	oauthWaitDone        bool
	oauthWaitDoneFn      func() bool
	waitOpenAICodexFn    func(context.Context, string) (rpc.AuthLoginOpenAICodexWaitResponse, error)
	prepareAuthFileFn    func(context.Context, string) (rpc.AuthPrepareFileResponse, error)
	localSecretStore     rpc.LocalSecretStoreStatus
	authStatusErr        error
	authStatusCalls      int
	setSecretErr         error
	authStatusAfterSet   map[string]rpc.AuthProviderStatus
	clearSecretErr       error
	setSessionNameErr    error
	sessionPreview       rpc.SessionPreviewResponse
	sessionPreviewErr    error
	restarts             int
	lastSetSecret        rpc.AuthSetPayload
	lastCleared          string
	lastNamedSession     string
	lastSessionName      string
	lastEnqueuedRun      rpc.RunEnqueuePayload
	lastInterruptedRun   rpc.RunInterruptPayload
	lastPermissionSet    rpc.PermissionSetPayload
	lastApprovalDecision rpc.ApprovalDecision
}

func newStubBackend() *stubBackend {
	return &stubBackend{
		modelCatalog: *testModelCatalog(),
		authStatuses: map[string]rpc.AuthProviderStatus{},
		oauthStatuses: map[string]rpc.OAuthProviderStatus{
			"openai-codex": {
				Provider: "openai-codex",
				LoggedIn: false,
			},
		},
		authStatusAfterSet: map[string]rpc.AuthProviderStatus{},
		logfireStatus: rpc.TraceLogfireStatusResponse{
			Installed:             true,
			CredentialsConfigured: true,
		},
		localSecretStore: rpc.LocalSecretStoreStatus{
			Available:     true,
			FileStorePath: filepath.Join(os.TempDir(), "jaca-auth.json"),
		},
		permissionState: rpc.PermissionState{
			SandboxPolicy:  rpc.SandboxPolicy{Mode: "danger_full_access", NetworkAccess: "enabled"},
			ApprovalPolicy: rpc.ApprovalPolicy{Mode: "never"},
			EffectiveCapabilities: rpc.EffectiveCapabilities{
				FilesystemAccess:   "full_access",
				NetworkAccess:      "enabled",
				ExecutionIsolation: "unsandboxed",
				ApprovalMode:       "never",
			},
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
func (b *stubBackend) CreateSession(_ context.Context) (rpc.SessionCreateResponse, error) {
	return rpc.SessionCreateResponse{
		SessionID: "session",
		ProjectDocs: []rpc.WorkspaceProjectDoc{
			{Filename: "AGENTS.md"},
		},
	}, nil
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
func (b *stubBackend) PrepareAuthFile(
	ctx context.Context,
	provider string,
) (rpc.AuthPrepareFileResponse, error) {
	if b.prepareAuthFileFn != nil {
		return b.prepareAuthFileFn(ctx, provider)
	}
	return rpc.AuthPrepareFileResponse{
		Provider:     provider,
		EnvKey:       authEnvKey(provider),
		FilePath:     b.localSecretStore.FileStorePath,
		Created:      true,
		FileSnippet:  authFileSnippet(authEnvKey(provider)),
		EntrySnippet: authFileEntrySnippet(authEnvKey(provider)),
	}, nil
}
func (b *stubBackend) AuthStatus(_ context.Context) (rpc.AuthStatusResponse, error) {
	b.authStatusCalls++
	if b.authStatusErr != nil {
		return rpc.AuthStatusResponse{}, b.authStatusErr
	}
	providers := []string{"openai", "anthropic"}
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
		OAuthProviders: []rpc.OAuthProviderStatus{
			b.oauthStatuses["openai-codex"],
		},
	}, nil
}

func (b *stubBackend) TraceLogfireStatus(_ context.Context) (rpc.TraceLogfireStatusResponse, error) {
	return b.logfireStatus, nil
}

func (b *stubBackend) PermissionGet(_ context.Context, sessionID string) (rpc.PermissionGetResponse, error) {
	return rpc.PermissionGetResponse{
		SessionID:       sessionID,
		PermissionState: b.permissionState,
	}, nil
}

func (b *stubBackend) PermissionSet(
	_ context.Context,
	sessionID string,
	sandboxPolicy *rpc.SandboxPolicy,
	approvalPolicy *rpc.ApprovalPolicy,
) (rpc.PermissionSetResponse, error) {
	b.lastPermissionSet = rpc.PermissionSetPayload{
		SessionID:      sessionID,
		SandboxPolicy:  sandboxPolicy,
		ApprovalPolicy: approvalPolicy,
	}
	if sandboxPolicy != nil {
		b.permissionState.SandboxPolicy = *sandboxPolicy
	}
	if approvalPolicy != nil {
		b.permissionState.ApprovalPolicy = *approvalPolicy
		b.permissionState.EffectiveCapabilities.ApprovalMode = approvalPolicy.Mode
	}
	return rpc.PermissionSetResponse{
		SessionID:       sessionID,
		PermissionState: b.permissionState,
	}, nil
}

func (b *stubBackend) ApprovalSubmit(
	_ context.Context,
	sessionID string,
	decision rpc.ApprovalDecision,
) (rpc.ApprovalSubmitResponse, error) {
	b.lastApprovalDecision = decision
	return rpc.ApprovalSubmitResponse{
		SessionID: sessionID,
		Decision:  decision,
	}, nil
}

func (b *stubBackend) StartOpenAICodexLogin(_ context.Context) (rpc.AuthLoginOpenAICodexStartResponse, error) {
	return rpc.AuthLoginOpenAICodexStartResponse{
		FlowID:       "flow-1",
		AuthURL:      "https://auth.example.test/login",
		Instructions: "If JACA does not finish automatically, paste the one-time code shown in the browser here.",
	}, nil
}

func (b *stubBackend) CompleteOpenAICodexLogin(
	_ context.Context,
	flowID string,
	callbackOrCode string,
) (rpc.AuthLoginOpenAICodexCompleteResponse, error) {
	if flowID == "" || callbackOrCode == "" {
		return rpc.AuthLoginOpenAICodexCompleteResponse{}, fmt.Errorf("missing login completion payload")
	}
	accountID := "acct-test"
	expiresAt := time.Now().Add(time.Hour).UnixMilli()
	status := rpc.OAuthProviderStatus{
		Provider:  "openai-codex",
		LoggedIn:  true,
		AccountID: &accountID,
		ExpiresAt: &expiresAt,
	}
	b.oauthStatuses["openai-codex"] = status
	return rpc.AuthLoginOpenAICodexCompleteResponse{Status: status}, nil
}

func (b *stubBackend) WaitOpenAICodexLogin(
	ctx context.Context,
	flowID string,
) (rpc.AuthLoginOpenAICodexWaitResponse, error) {
	if b.waitOpenAICodexFn != nil {
		return b.waitOpenAICodexFn(ctx, flowID)
	}
	if flowID == "" {
		return rpc.AuthLoginOpenAICodexWaitResponse{}, fmt.Errorf("missing flow id")
	}
	for {
		done := b.oauthWaitDone
		if b.oauthWaitDoneFn != nil {
			done = b.oauthWaitDoneFn()
		}
		if done {
			accountID := "acct-test"
			expiresAt := time.Now().Add(time.Hour).UnixMilli()
			status := rpc.OAuthProviderStatus{
				Provider:  "openai-codex",
				LoggedIn:  true,
				AccountID: &accountID,
				ExpiresAt: &expiresAt,
			}
			b.oauthStatuses["openai-codex"] = status
			return rpc.AuthLoginOpenAICodexWaitResponse{Status: status}, nil
		}
		select {
		case <-ctx.Done():
			return rpc.AuthLoginOpenAICodexWaitResponse{}, ctx.Err()
		case <-time.After(10 * time.Millisecond):
		}
	}
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
		Source:           "file",
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
	case "openai":
		envKey = "OPENAI_API_KEY"
	case "anthropic":
		envKey = "ANTHROPIC_API_KEY"
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
	if secretConfigured {
		reason = "ok"
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
	case "openai":
		return "OPENAI_API_KEY"
	case "anthropic":
		return "ANTHROPIC_API_KEY"
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
		Model:         "openai-responses:gpt-5.4",
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

func newTestModelWithAvailableUpdate(latestVersion string, command ...string) *model {
	m := New(Options{
		AppVersion: "0.1.11",
		AvailableUpdate: &UpdateNotice{
			LatestVersion: latestVersion,
			Command:       append([]string{}, command...),
		},
		Model:         "openai-responses:gpt-5.4",
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
	updated, _ = m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:         "session_queue_state",
		NextPrompts:  nil,
		LaterPrompts: []string{"follow up"},
	}})
	m = updated.(*model)
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
	updated, _ = m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:         "session_queue_state",
		NextPrompts:  []string{"be more concise"},
		LaterPrompts: nil,
	}})
	m = updated.(*model)
	rendered := stripANSI(m.View())
	for _, want := range []string{"After current tool phase", "1 queued", "↳ be more concise"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("queued steer preview missing %q in %q", want, rendered)
		}
	}
}

func TestEnterWhileStreamingDoesNotQueueBlockedSlashCommand(t *testing.T) {
	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.streaming = true
	m.sessionID = "session-123"
	m.textInput.SetValue("/model openai-responses:gpt-5.4-mini")
	m.textInput.Focus()

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd != nil {
		t.Fatal("blocked live slash command should not enqueue")
	}
	if got := m.textInput.Value(); got != "/model openai-responses:gpt-5.4-mini" {
		t.Fatalf("textInput.Value() = %q, want blocked command to stay in composer", got)
	}
	if backend.lastEnqueuedRun.Mode != "" || backend.lastEnqueuedRun.Prompt != "" {
		t.Fatalf("unexpected queued run: %#v", backend.lastEnqueuedRun)
	}
	if !strings.Contains(m.promptFooterNotice, "/model unavailable during an active run") {
		t.Fatalf("promptFooterNotice = %q", m.promptFooterNotice)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "model set to") {
		t.Fatalf("model command should not execute while streaming: %q", stripANSI(m.transcript.Render()))
	}
}

func TestTabWhileStreamingDoesNotQueueBlockedSlashCommand(t *testing.T) {
	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.streaming = true
	m.sessionID = "session-123"
	m.textInput.SetValue("/thinking high")
	m.textInput.Focus()

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyTab})
	m = updated.(*model)
	if cmd != nil {
		t.Fatal("blocked live slash command should not queue as follow-up")
	}
	if got := m.textInput.Value(); got != "/thinking high" {
		t.Fatalf("textInput.Value() = %q, want blocked command to stay in composer", got)
	}
	if backend.lastEnqueuedRun.Mode != "" || backend.lastEnqueuedRun.Prompt != "" {
		t.Fatalf("unexpected queued run: %#v", backend.lastEnqueuedRun)
	}
	if !strings.Contains(m.promptFooterNotice, "/thinking unavailable during an active run") {
		t.Fatalf("promptFooterNotice = %q", m.promptFooterNotice)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "thinking set to") {
		t.Fatalf("thinking command should not execute while streaming: %q", stripANSI(m.transcript.Render()))
	}
}

func TestEnterWhileStreamingDoesNotExecuteReadOnlySlashCommand(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.sessionID = "session-123"
	m.sessionName = "test-session"
	m.textInput.SetValue("/session")
	m.textInput.Focus()

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd != nil {
		t.Fatal("slash command should not execute during active run")
	}
	if got := m.textInput.Value(); got != "/session" {
		t.Fatalf("textInput.Value() = %q, want blocked command to stay in composer", got)
	}
	if got := len(m.promptHistory); got != 0 {
		t.Fatalf("promptHistory len = %d, want 0", got)
	}
	if !strings.Contains(m.promptFooterNotice, "/session unavailable during an active run") {
		t.Fatalf("promptFooterNotice = %q", m.promptFooterNotice)
	}
	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "note  session") || strings.Contains(rendered, "session: test-session") {
		t.Fatalf("/session should not execute while streaming: %q", rendered)
	}
}

func TestPermissionSlashShowsBackendState(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.sessionID = "session-123"

	updated, cmd := m.submitSlashCommand("/permission show", false)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/permission show should query backend state")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if m.permissionState == nil {
		t.Fatal("permissionState = nil, want cached backend state")
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"permission: full_access",
		"effective capabilities: filesystem=full_access network=enabled isolation=unsandboxed approval=never",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("permission render missing %q in %q", want, rendered)
		}
	}
}

func TestPermissionSlashShowsWorkspaceDefaultStateWithoutSession(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	updated, cmd := m.submitSlashCommand("/permission show", false)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/permission show should query backend state without an active session")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if m.permissionState == nil {
		t.Fatal("permissionState = nil, want cached backend state")
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"permission: full_access",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("permission render missing %q in %q", want, rendered)
		}
	}
}

func TestPermissionSlashUpdatesBackendPolicy(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.sessionID = "session-123"

	updated, cmd := m.submitSlashCommand("/permission default", false)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/permission default should call backend")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastPermissionSet.SessionID != "session-123" {
		t.Fatalf("lastPermissionSet.SessionID = %q", backend.lastPermissionSet.SessionID)
	}
	if backend.lastPermissionSet.SandboxPolicy == nil || backend.lastPermissionSet.SandboxPolicy.Mode != "workspace_write" {
		t.Fatalf("lastPermissionSet.SandboxPolicy = %#v", backend.lastPermissionSet.SandboxPolicy)
	}
	if backend.lastPermissionSet.ApprovalPolicy == nil || backend.lastPermissionSet.ApprovalPolicy.Mode != "on_escalation" {
		t.Fatalf("lastPermissionSet.ApprovalPolicy = %#v", backend.lastPermissionSet.ApprovalPolicy)
	}
	if m.permissionState == nil || m.permissionState.ApprovalPolicy.Mode != "on_escalation" {
		t.Fatalf("cached permission state = %#v", m.permissionState)
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"permission state updated",
		"permission: default",
		"effective capabilities: filesystem=full_access network=enabled isolation=unsandboxed approval=on_escalation",
		"selected sandbox policy is staged; the restricted local executor backend is not wired yet",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("permission render missing %q in %q", want, rendered)
		}
	}
}

func TestPermissionSlashUpdatesWorkspaceDefaultPolicyWithoutSession(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	updated, cmd := m.submitSlashCommand("/permission default", false)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/permission default should call backend without an active session")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastPermissionSet.SessionID != "" {
		t.Fatalf("lastPermissionSet.SessionID = %q, want empty", backend.lastPermissionSet.SessionID)
	}
	if backend.lastPermissionSet.SandboxPolicy == nil || backend.lastPermissionSet.SandboxPolicy.Mode != "workspace_write" {
		t.Fatalf("lastPermissionSet.SandboxPolicy = %#v", backend.lastPermissionSet.SandboxPolicy)
	}
	if backend.lastPermissionSet.ApprovalPolicy == nil || backend.lastPermissionSet.ApprovalPolicy.Mode != "on_escalation" {
		t.Fatalf("lastPermissionSet.ApprovalPolicy = %#v", backend.lastPermissionSet.ApprovalPolicy)
	}
	if m.permissionState == nil || m.permissionState.ApprovalPolicy.Mode != "on_escalation" {
		t.Fatalf("cached permission state = %#v", m.permissionState)
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"permission state updated",
		"permission: default",
		"effective capabilities: filesystem=full_access network=enabled isolation=unsandboxed approval=on_escalation",
		"selected sandbox policy is staged; the restricted local executor backend is not wired yet",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("permission render missing %q in %q", want, rendered)
		}
	}
}

func TestPermissionSlashUpdatesFullAccessPolicy(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	updated, cmd := m.submitSlashCommand("/permission full_access", false)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/permission full_access should call backend")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastPermissionSet.SandboxPolicy == nil || backend.lastPermissionSet.SandboxPolicy.Mode != "danger_full_access" {
		t.Fatalf("lastPermissionSet.SandboxPolicy = %#v", backend.lastPermissionSet.SandboxPolicy)
	}
	if backend.lastPermissionSet.ApprovalPolicy == nil || backend.lastPermissionSet.ApprovalPolicy.Mode != "never" {
		t.Fatalf("lastPermissionSet.ApprovalPolicy = %#v", backend.lastPermissionSet.ApprovalPolicy)
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"permission state updated",
		"permission: full_access",
		"effective capabilities: filesystem=full_access network=enabled isolation=unsandboxed approval=never",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("permission render missing %q in %q", want, rendered)
		}
	}
}

func TestPermissionSlashSuggestionsMarkCurrentPreset(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	state := backend.permissionState
	m.permissionState = &state
	rows := m.permissionSlashSuggestions()

	if len(rows) != 2 {
		t.Fatalf("len(rows) = %d, want 2", len(rows))
	}
	if rows[0].Current {
		t.Fatal("default should not be current when backend state is full_access")
	}
	if !rows[1].Current {
		t.Fatal("full_access should be marked current")
	}

	defaultState := backend.permissionState
	defaultState.SandboxPolicy.Mode = "workspace_write"
	defaultState.ApprovalPolicy.Mode = "on_escalation"
	m.permissionState = &defaultState
	rows = m.permissionSlashSuggestions()

	if !rows[0].Current {
		t.Fatal("default should be marked current")
	}
	if rows[1].Current {
		t.Fatal("full_access should not be current when default is active")
	}
}

func TestPermissionSlashMenuRendersCurrentBadge(t *testing.T) {
	rendered := stripANSI(renderSlashMenu(slashMenuState{
		Mode: slashMenuArguments,
		Rows: []slashSuggestion{
			{
				Value:       "default",
				Description: "Recommended mode",
				Current:     true,
			},
			{
				Value:       "full_access",
				Description: "Danger mode",
			},
		},
		Selected: 0,
	}))

	if !strings.Contains(rendered, "default") {
		t.Fatalf("rendered slash menu missing default row: %q", rendered)
	}
	if !strings.Contains(rendered, "[current]") {
		t.Fatalf("rendered slash menu missing current badge: %q", rendered)
	}
}

func TestSilentPermissionStateHydrationMarksCurrentPresetWithoutTranscriptNoise(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	defaultState := backend.permissionState
	defaultState.SandboxPolicy.Mode = "workspace_write"
	defaultState.ApprovalPolicy.Mode = "on_escalation"
	defaultState.EffectiveCapabilities = rpc.EffectiveCapabilities{
		FilesystemAccess:   "workspace_write",
		NetworkAccess:      "restricted",
		ExecutionIsolation: "sandboxed",
		ApprovalMode:       "on_escalation",
	}

	updated, _ := m.Update(permissionStateLoadedMsg{
		State:   defaultState,
		Updated: false,
		Display: false,
	})
	m = updated.(*model)

	rows := m.permissionSlashSuggestions()
	if !rows[0].Current {
		t.Fatal("default should be marked current after silent permission-state hydration")
	}
	if rows[1].Current {
		t.Fatal("full_access should not be marked current after silent permission-state hydration")
	}

	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "permission:") {
		t.Fatalf("silent permission-state hydration should not write transcript note, got %q", rendered)
	}
}

func TestCurrentViewModelUsesFullAccessPermissionPreset(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)

	fullAccessState := backend.permissionState
	fullAccessState.SandboxPolicy.Mode = "danger_full_access"
	fullAccessState.SandboxPolicy.NetworkAccess = "enabled"
	fullAccessState.ApprovalPolicy.Mode = "never"
	fullAccessState.EffectiveCapabilities = rpc.EffectiveCapabilities{
		FilesystemAccess:   "full_access",
		NetworkAccess:      "enabled",
		ExecutionIsolation: "unsandboxed",
		ApprovalMode:       "never",
	}
	m.permissionState = &fullAccessState

	vm := m.currentViewModel()
	if vm.PermissionPreset != "full_access" {
		t.Fatalf("vm.PermissionPreset = %q, want full_access", vm.PermissionPreset)
	}
}

func TestApproveSlashWorksDuringStreaming(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.streaming = true
	m.sessionID = "session-123"
	m.pendingApproval = &rpc.ApprovalRequest{
		RequestID:   "approval-1",
		RequestKind: "command_execution",
		Reason:      "allow shell command: ls",
		Command:     "ls",
		Cwd:         "/workspace",
		ShellFamily: "posix",
		RequestedCapabilities: rpc.EffectiveCapabilities{
			FilesystemAccess:   "full_access",
			NetworkAccess:      "enabled",
			ExecutionIsolation: "unsandboxed",
			ApprovalMode:       "always",
		},
	}

	updated, cmd := m.submitSlashCommand("/approve", true)
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("/approve should be allowed during streaming")
	}
	msg := cmd()
	updated, _ = m.Update(msg)
	m = updated.(*model)

	if backend.lastApprovalDecision.RequestID != "approval-1" {
		t.Fatalf("lastApprovalDecision.RequestID = %q", backend.lastApprovalDecision.RequestID)
	}
	if backend.lastApprovalDecision.Decision != "approved" {
		t.Fatalf("lastApprovalDecision.Decision = %q", backend.lastApprovalDecision.Decision)
	}
	if m.pendingApproval != nil {
		t.Fatalf("pendingApproval = %#v, want nil after submit", m.pendingApproval)
	}
}

func TestApprovalEventUpdatesFooterAndTranscript(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.height = 20
	m.transcript.WriteLine("prior transcript context")
	m.refreshViewport()

	updated, cmd := m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:  "approval_requested",
		RunID: "run-1",
		Request: &rpc.ApprovalRequest{
			RequestID:   "approval-1",
			RequestKind: "command_execution",
			Reason:      "allow shell command: ls",
			Command:     "ls",
			Cwd:         "/workspace",
			ShellFamily: "posix",
			RequestedCapabilities: rpc.EffectiveCapabilities{
				FilesystemAccess:   "full_access",
				NetworkAccess:      "enabled",
				ExecutionIsolation: "unsandboxed",
				ApprovalMode:       "always",
			},
		},
	}})
	m = updated.(*model)
	if cmd != nil {
		t.Fatal("approval_requested should pause async listening until the user decides")
	}

	if got := m.currentPromptFooter(); got != "" {
		t.Fatalf("currentPromptFooter() = %q", got)
	}
	if got := stripANSI(m.transcript.Render()); !strings.Contains(got, "prior transcript context") {
		t.Fatalf("transcript missing preserved context in %q", got)
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{
		"Command approval required",
		"allow shell command: ls",
		"request kind: command execution",
		"requested posture: fs=full_access, net=enabled, exec=unsandboxed",
		"command: ls",
		"cwd: /workspace",
		"shell: posix",
		"1. Approve and continue",
		"2. Deny request",
		"Select an action for this request.",
		"Enter confirms the selected action. Esc denies immediately.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("approval request render missing %q in %q", want, rendered)
		}
	}
	if strings.Contains(rendered, "note  approval") {
		t.Fatalf("approval request render should not duplicate transcript note in %q", rendered)
	}

	updated, _ = m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:  "approval_resolved",
		RunID: "run-1",
		Decision: &rpc.ApprovalDecision{
			RequestID: "approval-1",
			Decision:  "approved",
		},
	}})
	m = updated.(*model)

	if got := m.currentPromptFooter(); got != "" {
		t.Fatalf("currentPromptFooter() after resolution = %q, want empty", got)
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "note  approval") {
		t.Fatalf("resolved approval should not add transcript note in %q", got)
	}
}

func TestApprovalSubmitResumesAsyncListening(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.asyncCh = make(chan tea.Msg, 1)
	m.pendingApproval = &rpc.ApprovalRequest{
		RequestID:   "approval-1",
		RequestKind: "command_execution",
		Reason:      "allow shell command: pytest -q",
		Command:     "pytest -q",
		Cwd:         "/workspace",
		ShellFamily: "posix",
		RequestedCapabilities: rpc.EffectiveCapabilities{
			FilesystemAccess:   "full_access",
			NetworkAccess:      "enabled",
			ExecutionIsolation: "unsandboxed",
			ApprovalMode:       "always",
		},
	}
	m.approvalPaused = true

	m.asyncCh <- runEventMsg{Event: rpc.RunEvent{
		Type:  "approval_resolved",
		RunID: "run-1",
		Decision: &rpc.ApprovalDecision{
			RequestID: "approval-1",
			Decision:  "approved",
		},
	}}

	updated, cmd := m.Update(approvalSubmittedMsg{
		Decision: rpc.ApprovalDecision{
			RequestID: "approval-1",
			Decision:  "approved",
		},
	})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("approvalSubmittedMsg should resume async listening while the run is still streaming")
	}

	msg := cmd()
	runEvent, ok := msg.(runEventMsg)
	if !ok {
		t.Fatalf("cmd() = %T, want runEventMsg", msg)
	}
	if runEvent.Event.Type != "approval_resolved" {
		t.Fatalf("runEvent.Type = %q, want approval_resolved", runEvent.Event.Type)
	}
}

func TestApprovalSubmitShowsWaitingNoticeUntilBackendResumes(t *testing.T) {
	m := newTestModel()
	m.streaming = true
	m.pendingApproval = &rpc.ApprovalRequest{
		RequestID:   "approval-1",
		RequestKind: "command_execution",
		Reason:      "allow shell command: pytest -q",
		Command:     "pytest -q",
		Cwd:         "/workspace",
		ShellFamily: "posix",
		RequestedCapabilities: rpc.EffectiveCapabilities{
			FilesystemAccess:   "full_access",
			NetworkAccess:      "enabled",
			ExecutionIsolation: "unsandboxed",
			ApprovalMode:       "always",
		},
	}
	m.approvalPaused = true

	updated, _ := m.Update(approvalSubmittedMsg{
		Decision: rpc.ApprovalDecision{
			RequestID: "approval-1",
			Decision:  "approved",
		},
	})
	m = updated.(*model)

	if got := m.currentPromptFooter(); got != "approval sent; waiting for tool activity" {
		t.Fatalf("currentPromptFooter() = %q", got)
	}
}

func TestApprovalInlineEnterApprovesPendingAction(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.streaming = true
	m.sessionID = "session-123"
	m.pendingApproval = &rpc.ApprovalRequest{
		RequestID:   "approval-1",
		RequestKind: "command_execution",
		Reason:      "allow shell command: ls",
		Command:     "ls",
		Cwd:         "/workspace",
		ShellFamily: "posix",
		RequestedCapabilities: rpc.EffectiveCapabilities{
			FilesystemAccess:   "full_access",
			NetworkAccess:      "enabled",
			ExecutionIsolation: "unsandboxed",
			ApprovalMode:       "always",
		},
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if backend.lastApprovalDecision.RequestID != "approval-1" {
		t.Fatalf("lastApprovalDecision.RequestID = %q", backend.lastApprovalDecision.RequestID)
	}
	if backend.lastApprovalDecision.Decision != "approved" {
		t.Fatalf("lastApprovalDecision.Decision = %q", backend.lastApprovalDecision.Decision)
	}
	if m.pendingApproval != nil {
		t.Fatalf("pendingApproval = %#v, want nil after approve", m.pendingApproval)
	}
}

func TestApprovalInlineEscapeDeniesPendingAction(t *testing.T) {
	backend := newStubBackend()
	m := newTestModelWithBackend(backend)
	m.streaming = true
	m.sessionID = "session-123"
	m.pendingApproval = &rpc.ApprovalRequest{
		RequestID:   "approval-1",
		RequestKind: "command_execution",
		Reason:      "allow shell command: ls",
		Command:     "ls",
		Cwd:         "/workspace",
		ShellFamily: "posix",
		RequestedCapabilities: rpc.EffectiveCapabilities{
			FilesystemAccess:   "full_access",
			NetworkAccess:      "enabled",
			ExecutionIsolation: "unsandboxed",
			ApprovalMode:       "always",
		},
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEsc})

	if backend.lastApprovalDecision.RequestID != "approval-1" {
		t.Fatalf("lastApprovalDecision.RequestID = %q", backend.lastApprovalDecision.RequestID)
	}
	if backend.lastApprovalDecision.Decision != "denied" {
		t.Fatalf("lastApprovalDecision.Decision = %q", backend.lastApprovalDecision.Decision)
	}
	if m.pendingApproval != nil {
		t.Fatalf("pendingApproval = %#v, want nil after deny", m.pendingApproval)
	}
}

func TestQueuedPromptBatchSubmittedShowsUserTurnInTranscript(t *testing.T) {
	m := newTestModel()
	updated, _ := m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:    "session_queued_prompt_batch_submitted",
		Prompts: []string{"tighten the answer", "add tests"},
		Mode:    "later",
	}})
	m = updated.(*model)

	rendered := stripANSI(m.View())
	for _, want := range []string{"│ tighten the answer", "add tests"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("queued prompt submission missing %q in %q", want, rendered)
		}
	}
}

func TestQueuedPromptBatchSubmittedClearsShelfImmediately(t *testing.T) {
	m := newTestModel()
	m.queuedPreview.Next = []string{"run go tests"}
	m.queuedPreview.Later = []string{"what is compaction"}

	updated, _ := m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:    "session_queued_prompt_batch_submitted",
		Prompts: []string{"run go tests"},
		Mode:    "next",
	}})
	m = updated.(*model)

	if len(m.queuedPreview.Next) != 0 {
		t.Fatalf("expected next queue to clear immediately, got %#v", m.queuedPreview.Next)
	}
	if len(m.queuedPreview.Later) != 1 || m.queuedPreview.Later[0] != "what is compaction" {
		t.Fatalf("expected later queue to remain intact, got %#v", m.queuedPreview.Later)
	}

	rendered := stripANSI(m.View())
	if strings.Contains(rendered, "After current tool phase") || strings.Contains(rendered, "↳ run go tests") {
		t.Fatalf("expected submitted next prompt to disappear from queue shelf, got %q", rendered)
	}
	if !strings.Contains(rendered, "│ run go tests") {
		t.Fatalf("expected submitted prompt to appear in transcript, got %q", rendered)
	}
}

func TestQueuedPromptBatchSubmittedRemovesPromptFromBothBuckets(t *testing.T) {
	m := newTestModel()
	m.queuedPreview.Next = []string{"run go tests"}
	m.queuedPreview.Later = []string{"run go tests", "what is compaction"}

	updated, _ := m.Update(runEventMsg{Event: rpc.RunEvent{
		Type:    "session_queued_prompt_batch_submitted",
		Prompts: []string{"run go tests"},
		Mode:    "later",
	}})
	m = updated.(*model)

	if len(m.queuedPreview.Next) != 0 {
		t.Fatalf("expected submitted prompt removed from next queue too, got %#v", m.queuedPreview.Next)
	}
	if len(m.queuedPreview.Later) != 1 || m.queuedPreview.Later[0] != "what is compaction" {
		t.Fatalf("expected later queue to retain only unsent prompts, got %#v", m.queuedPreview.Later)
	}
}

func testModelCatalog() *rpc.ModelCatalogResponse {
	return &rpc.ModelCatalogResponse{
		Providers: []rpc.ModelCatalogProvider{
			{
				Provider:       "openai",
				DefaultModelID: "openai-responses:gpt-5.4",
				Models: []rpc.ModelCatalogModel{
					{ModelID: "openai-responses:gpt-5.4", Description: "Default GPT-5.4 Responses path"},
					{ModelID: "openai-responses:gpt-5.4-mini", Description: "Faster GPT-5.4 mini Responses path"},
					{ModelID: "openai-responses:gpt-5.3-codex", Description: "Codex-optimized GPT-5.3 Responses path"},
					{ModelID: "openai-responses:gpt-5-codex", Description: "Experimental ChatGPT subscription Codex path"},
					{ModelID: "openai-responses:gpt-5-chatgpt", Description: "Experimental ChatGPT subscription GPT-5 path"},
					{ModelID: "openai-responses:gpt-5-mini-chatgpt", Description: "Experimental ChatGPT subscription GPT-5 mini path"},
					{ModelID: "openai-responses:gpt-5.1-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.1 path"},
					{ModelID: "openai-responses:gpt-5.1-codex-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.1 Codex path"},
					{ModelID: "openai-responses:gpt-5.1-codex-mini-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.1 Codex Mini path"},
					{ModelID: "openai-responses:gpt-5.1-codex-max-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.1 Codex Max path"},
					{ModelID: "openai-responses:gpt-5.2-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.2 path"},
					{ModelID: "openai-responses:gpt-5.2-codex-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.2 Codex path"},
					{ModelID: "openai-responses:gpt-5.3-codex-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.3 Codex path"},
					{ModelID: "openai-responses:gpt-5.4-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.4 path"},
					{ModelID: "openai-responses:gpt-5.4-mini-chatgpt", Description: "Experimental ChatGPT subscription GPT-5.4 mini path"},
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
		},
	}
}

func sendKey(m *model, msg tea.KeyMsg) *model {
	updated, cmd := m.Update(msg)
	m = updated.(*model)
	return runTestCmd(m, cmd)
}

func sendRunes(m *model, value string) *model {
	for _, r := range value {
		m = sendKey(m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	return m
}

func runTestCmd(m *model, cmd tea.Cmd) *model {
	if cmd == nil {
		return m
	}
	msg := cmd()
	if msg == nil {
		return m
	}
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, child := range batch {
			m = runTestCmd(m, child)
		}
		return m
	}
	updated, next := m.Update(msg)
	return runTestCmd(updated.(*model), next)
}

func TestShouldPromptForUpdateHonorsSkipAndSnoozeRules(t *testing.T) {
	now := time.Date(2026, 4, 12, 12, 0, 0, 0, time.UTC)

	if !shouldPromptForUpdate(map[string]string{}, "0.1.12", now) {
		t.Fatal("empty config should prompt for a newer version")
	}
	if shouldPromptForUpdate(map[string]string{"update_skip_version": "0.1.12"}, "0.1.12", now) {
		t.Fatal("same skipped version should stay suppressed")
	}
	if !shouldPromptForUpdate(map[string]string{"update_skip_version": "0.1.12"}, "0.1.13", now) {
		t.Fatal("newer version should ignore an older skipped version")
	}
	if shouldPromptForUpdate(
		map[string]string{"update_snooze_until": now.Add(time.Hour).Format(time.RFC3339)},
		"0.1.12",
		now,
	) {
		t.Fatal("active snooze window should suppress the prompt")
	}
	if !shouldPromptForUpdate(
		map[string]string{"update_snooze_until": now.Add(-time.Hour).Format(time.RFC3339)},
		"0.1.12",
		now,
	) {
		t.Fatal("expired snooze window should allow the prompt")
	}
}

func TestUpdateOverlayDefaultsToLaterAndPersistsSnooze(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModelWithAvailableUpdate(
		"0.1.12",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	if !m.update.Active {
		t.Fatal("expected update overlay to open for a newer published version")
	}
	if m.update.Selected != 1 {
		t.Fatalf("update.Selected = %d, want 1 for Later", m.update.Selected)
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if m.update.Active {
		t.Fatal("expected update overlay to close after choosing Later")
	}
	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("config.Load() returned error: %v", err)
	}
	rawUntil := strings.TrimSpace(cfg["update_snooze_until"])
	if rawUntil == "" {
		t.Fatal("expected update_snooze_until to be persisted")
	}
	until, err := time.Parse(time.RFC3339, rawUntil)
	if err != nil {
		t.Fatalf("time.Parse() returned error: %v", err)
	}
	if !until.After(time.Now().UTC().Add(23 * time.Hour)) {
		t.Fatalf("snooze until = %s, want roughly 24h ahead", until)
	}
}

func TestUpdateOverlayCanSkipOnlyCurrentVersion(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModelWithAvailableUpdate(
		"0.1.12",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("3")})
	sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("config.Load() returned error: %v", err)
	}
	if got := cfg["update_skip_version"]; got != "0.1.12" {
		t.Fatalf("update_skip_version = %q, want %q", got, "0.1.12")
	}

	sameVersion := newTestModelWithAvailableUpdate(
		"0.1.12",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	if sameVersion.update.Active {
		t.Fatal("same skipped version should not reopen the update overlay")
	}

	newerVersion := newTestModelWithAvailableUpdate(
		"0.1.13",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	if !newerVersion.update.Active {
		t.Fatal("newer version should reopen the update overlay")
	}
}

func TestUpdateOverlayCanRequestExternalUpdate(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModelWithAvailableUpdate(
		"0.1.12",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("1")})
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)

	quitMsg := cmd()
	if _, ok := quitMsg.(tea.QuitMsg); !ok {
		t.Fatalf("cmd() = %#v, want tea.QuitMsg", quitMsg)
	}
	action := m.ExitAction()
	if action == nil {
		t.Fatal("expected update selection to record an external update action")
	}
	if action.Kind != ExternalActionUpdate {
		t.Fatalf("action.Kind = %q, want %q", action.Kind, ExternalActionUpdate)
	}
	if strings.Join(action.Command, " ") != "uv tool upgrade just-another-coding-agent" {
		t.Fatalf("action.Command = %#v", action.Command)
	}
}

func TestVersionSlashShowsAvailableUpgradeCommand(t *testing.T) {
	m := newTestModelWithAvailableUpdate(
		"0.1.12",
		"uv", "tool", "upgrade", "just-another-coding-agent",
	)
	m.update = updateState{}

	m = sendRunes(m, "/version")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"installed: 0.1.11",
		"available: 0.1.12",
		"uv tool upgrade just-another-coding-agent",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("missing %q in %q", want, rendered)
		}
	}
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

func TestNewResumedSessionSkipsFirstRunOnboardingWithoutResumeNote(t *testing.T) {
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
	if strings.Contains(rendered, "resumed auth-store-cleanup") {
		t.Fatalf("startup transcript should not repeat resumed session name: %q", rendered)
	}
	if strings.Contains(rendered, "0123456789abcdef0123456789abcdef") {
		t.Fatalf("startup transcript should not expose resumed session id: %q", rendered)
	}
}

func TestResumedSessionPreviewHydratesRecentHistory(t *testing.T) {
	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.sessionID = "0123456789abcdef0123456789abcdef"
	m.sessionName = "auth-store-cleanup"

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
	if !strings.Contains(rendered, "older history omitted") {
		t.Fatalf("resume preview truncation note missing from transcript: %q", rendered)
	}
	if !strings.Contains(rendered, "│ fix the auth store") {
		t.Fatalf("resumed user turn missing from transcript: %q", rendered)
	}
	if !strings.Contains(rendered, "I updated the auth store logic.") {
		t.Fatalf("resumed assistant turn missing from transcript: %q", rendered)
	}
}

func TestSessionPreviewActivityEntryHydratesBackendActivity(t *testing.T) {
	m := newTestModel()

	m.transcript.ApplySessionPreview(rpc.SessionPreviewResponse{
		SessionID: "1234",
		Entries: []rpc.SessionPreviewEntry{
			{Kind: "user", Text: "check shell commands"},
			{Kind: "activity", Text: "Shell - 5 commands - 1.2s"},
			{Kind: "assistant", Text: "The worktree is clean."},
		},
	})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"│ check shell commands",
		"* Shell - 5 commands - 1.2s",
		"The worktree is clean.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("preview transcript missing %q in %q", want, rendered)
		}
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
		"note  compacted",
		"Session has been compacted multiple times; continuity quality may degrade.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("transcript missing %q in %q", want, rendered)
		}
	}
	for _, absent := range []string{
		"compacting session...",
		"session compacted",
	} {
		if strings.Contains(rendered, absent) {
			t.Fatalf("transcript should not contain legacy compaction line %q in %q", absent, rendered)
		}
	}
}

func TestSessionPreviewInstructionsEntryWritesNote(t *testing.T) {
	m := newTestModel()

	m.transcript.ApplySessionPreview(rpc.SessionPreviewResponse{
		SessionID: "1234",
		Entries: []rpc.SessionPreviewEntry{
			{Kind: "instructions", Text: "loaded project instructions: AGENTS.md"},
		},
	})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"note  instructions",
		"loaded project instructions: AGENTS.md",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("preview transcript missing %q in %q", want, rendered)
		}
	}
}

func TestSessionPreviewInstructionsEntryPinsBeforeCurrentRun(t *testing.T) {
	m := newTestModel()
	m.transcript.WriteUserTurn("hello")

	m.transcript.ApplySessionPreview(rpc.SessionPreviewResponse{
		SessionID: "1234",
		Entries: []rpc.SessionPreviewEntry{
			{Kind: "instructions", Text: "loaded project instructions: AGENTS.md"},
		},
	})

	rendered := stripANSI(m.transcript.Render())
	instructionsIndex := strings.Index(rendered, "loaded project instructions: AGENTS.md")
	userIndex := strings.Index(rendered, "│ hello")
	if instructionsIndex == -1 || userIndex == -1 {
		t.Fatalf("expected instructions note and user turn in %q", rendered)
	}
	if instructionsIndex > userIndex {
		t.Fatalf("instructions note should appear before current run in %q", rendered)
	}
}

func TestSessionCreatedMsgPinsInstructionsWithoutPreviewReplay(t *testing.T) {
	m := newTestModel()
	m.transcript.WriteUserTurn("hello")
	m.transcript.completeAssistant("Hi! What would you like to work on?")

	updated, _ := m.Update(sessionCreatedMsg{
		Response: rpc.SessionCreateResponse{
			SessionID: "1234",
			ProjectDocs: []rpc.WorkspaceProjectDoc{
				{Filename: "AGENTS.md"},
			},
		},
	})
	m = updated.(*model)

	rendered := stripANSI(m.transcript.Render())
	if strings.Count(rendered, "│ hello") != 1 {
		t.Fatalf("expected one user turn in %q", rendered)
	}
	if strings.Count(rendered, "Hi! What would you like to work on?") != 1 {
		t.Fatalf("expected one assistant response in %q", rendered)
	}
	if !strings.Contains(rendered, "loaded project instructions: AGENTS.md") {
		t.Fatalf("missing instructions note in %q", rendered)
	}
}

func TestSlashShowsInlineCommandSuggestions(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/")

	rendered := stripANSI(m.View())
	for _, want := range []string{
		"/login",
		"/model",
		"/permission",
		"/approve",
		"/deny",
		"/version",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing slash suggestion %q in %q", want, rendered)
		}
	}
	if got := stripANSI(m.transcript.Render()); strings.Contains(got, "/login") {
		t.Fatalf("transcript changed while browsing slash suggestions: %q", got)
	}
}

func TestTabOnLoginSuggestionCommitsCommandAndShowsLoginOptions(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/log")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyTab})

	if got := m.textInput.Value(); got != "/login " {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/login ")
	}

	rendered := stripANSI(m.View())
	for _, want := range []string{"openai-codex"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("view missing login suggestion %q in %q", want, rendered)
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

func TestEnterOnModelArgumentSuggestionExecutesSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model ")
	updated, _ := m.Update(authStatusLoadedMsg{
		Status: status,
	})
	m = updated.(*model)
	if !m.slashMenuVisible() {
		t.Fatal("expected /model suggestions to be visible")
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "model set to gpt-5.4 | api") {
		t.Fatalf("transcript missing executed model change: %q", rendered)
	}
}

func TestTabOnModelSuggestionCommitsDisplayLabel(t *testing.T) {
	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}
	m := newTestModel()

	m = sendRunes(m, "/model gpt-5")
	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)
	if !m.slashMenuVisible() {
		t.Fatal("expected /model suggestions to be visible")
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyTab})

	if got := m.textInput.Value(); got != "/model gpt-5.4 | api" {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/model gpt-5.4 | api")
	}
}

func TestModelCommandAcceptsDisplayLabel(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")

	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}
	m := newTestModel()
	m.options.Backend = backend

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	updated, _ = m.submitSlashCommand("/model gpt-5.4 | api", false)
	m = updated.(*model)

	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4")
	}
}

func TestEnterOnHelpSuggestionExecutesCommand(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/he")
	if !m.slashMenuVisible() {
		t.Fatal("expected command suggestions to be visible")
	}

	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "note  commands") {
		t.Fatalf("help command did not execute from slash suggestion: %q", rendered)
	}
}

func TestLoginSlashSuggestionsIncludeSupportedLanes(t *testing.T) {
	m := newTestModel()

	m = sendRunes(m, "/login ")

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "openai-codex") {
		t.Fatalf("view missing openai-codex login suggestion in %q", rendered)
	}
	if !strings.Contains(rendered, "openai") {
		t.Fatalf("view missing openai login suggestion in %q", rendered)
	}
	if !strings.Contains(rendered, "anthropic") {
		t.Fatalf("view missing anthropic login suggestion in %q", rendered)
	}
}

func TestAuthStatusLoadRefreshesModelSlashSuggestions(t *testing.T) {
	m := newTestModel()
	backend := newStubBackend()
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m = sendRunes(m, "/model ")
	if m.slashMenuVisible() {
		t.Fatalf("slash menu should stay hidden before auth status arrives, got %#v", m.slashMenu)
	}

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	if !m.slashMenuVisible() {
		t.Fatal("expected /model slash suggestions to appear after auth status loads")
	}
	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "gpt-5.4 | api") {
		t.Fatalf("view missing model suggestions after auth status load in %q", rendered)
	}
}

func TestAuthStatusLoadMakesAvailableOAuthModelTheDefaultModelSuggestion(t *testing.T) {
	m := newTestModel()
	backend := newStubBackend()
	backend.authStatuses["openai"] = rpc.AuthProviderStatus{
		Provider:   "openai",
		Configured: false,
		Source:     "none",
	}
	backend.oauthStatuses["openai-codex"] = rpc.OAuthProviderStatus{
		Provider: "openai-codex",
		LoggedIn: true,
	}
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m = sendRunes(m, "/model ")

	updated, _ := m.Update(authStatusLoadedMsg{Status: status})
	m = updated.(*model)

	if !m.slashMenuVisible() {
		t.Fatal("expected /model slash suggestions to appear after auth status loads")
	}
	if len(m.slashMenu.Rows) == 0 {
		t.Fatal("expected non-empty /model slash suggestions")
	}
	if got := m.slashMenu.Rows[m.slashMenu.Selected].Value; got != "openai-responses:gpt-5-codex" {
		t.Fatalf("selected /model row = %q, want first available oauth model", got)
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
	if !strings.Contains(rendered, "> /model") {
		t.Fatalf("expected down arrow to move active slash selection in %q", rendered)
	}
}

func TestModelCommandPersistsSelection(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model openai-responses:gpt-5.4-mini")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai-responses:gpt-5.4-mini" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5.4-mini")
	}
	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"default_model": "openai-responses:gpt-5.4-mini"`) {
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
		"Connect JACA",
		"1. ChatGPT subscription",
		"2. OpenAI API key",
		"3. Anthropic API key",
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
		Model:         "openai-responses:gpt-5.4",
		WorkspaceRoot: "/workspace",
		Thinking:      "medium",
	}).(*model)

	if !m.onboarding.Active || m.onboarding.Kind != "provider" {
		t.Fatalf("onboarding state = %#v, want active provider chooser", m.onboarding)
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"Connect JACA", "1. ChatGPT subscription", "3. Anthropic API key"} {
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
		"Connect JACA",
		"1. ChatGPT subscription",
		"2. OpenAI API key",
		"3. Anthropic API key",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("first-run chooser missing %q in %q", want, rendered)
		}
	}
}

func TestMaybeStartOnboardingStartsOpenAICodexLoginCommandForOAuthModelWhenAnotherLaneIsAvailable(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	if err := os.MkdirAll(filepath.Join(home, ".jaca"), 0o755); err != nil {
		t.Fatalf("MkdirAll() returned error: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(home, ".jaca", "config.json"),
		[]byte(`{"default_provider":"openai","default_model":"openai-responses:gpt-5.4-chatgpt"}`),
		0o644,
	); err != nil {
		t.Fatalf("WriteFile() returned error: %v", err)
	}

	backend := newStubBackend()
	backend.authStatuses["openai"] = rpc.AuthProviderStatus{
		Provider:   "openai",
		Configured: true,
		Source:     "file",
		EnvKey:     "OPENAI_API_KEY",
	}
	status, err := backend.AuthStatus(context.Background())
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}

	m := newTestModel()
	m.options.Backend = backend
	m.authStatus = &status

	cmd := m.maybeStartOnboarding()
	if cmd == nil {
		t.Fatal("maybeStartOnboarding() should return login start command")
	}
	if m.login.Active {
		t.Fatal("ChatGPT onboarding login should stay on transcript path before RPC response")
	}

	updated, next := m.Update(cmd())
	m = updated.(*model)
	if next == nil {
		t.Fatal("expected follow-up polling command after starting ChatGPT login")
	}
	if m.login.FlowID == "" {
		t.Fatal("expected login flow id after starting ChatGPT login")
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

	if got := m.textInput.Value(); got != "/login " {
		t.Fatalf("textInput.Value() = %q, want %q", got, "/login ")
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{"openai-codex"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("login suggestion %q missing in %q", want, rendered)
		}
	}
}

func TestFirstRunChoosingOpenAIShowsAuthFileNote(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	authPath := filepath.Join(os.TempDir(), "jaca-auth.json")

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

	if m.onboarding.Active {
		t.Fatal("onboarding chooser should close after provider selection")
	}
	if m.auth.Active {
		t.Fatalf("auth state should stay inactive: %#v", m.auth)
	}
	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"Add your OpenAI API key to:",
		authPath,
		"Paste this into the empty file:",
		`"OPENAI_API_KEY": "..."`,
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("transcript missing %q in %q", want, rendered)
		}
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

func TestStartupAuthStatusShowsChooserForPersistedProviderWithoutAnyAvailableLane(t *testing.T) {
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

	if !m.onboarding.Active {
		t.Fatal("startup should reopen chooser when no login lane is available")
	}
	rendered := stripANSI(m.View())
	for _, want := range []string{
		"Connect JACA",
		"1. ChatGPT subscription",
		"2. OpenAI API key",
		"3. Anthropic API key",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("startup chooser missing %q in %q", want, rendered)
		}
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

func TestLoginSlashStartsBackgroundOpenAICodexLogin(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/login openai-codex")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("expected login start command")
	}
	updated, _ = m.Update(cmd())
	m = updated.(*model)

	if m.login.Active {
		t.Fatal("expected login overlay to close after URL is ready")
	}
	if got := m.login.Provider; got != "openai-codex" {
		t.Fatalf("login.Provider = %q, want openai-codex", got)
	}
	if got := m.login.FlowID; got != "flow-1" {
		t.Fatalf("login.FlowID = %q, want flow-1", got)
	}
	if !m.login.Waiting {
		t.Fatal("expected login flow to keep waiting for callback")
	}
	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "Open login link (auth.example.test)") {
		t.Fatalf("view missing shortened login link note: %q", rendered)
	}
	if strings.Contains(rendered, "Waiting for browser callback on http://localhost:1455/auth/callback") {
		t.Fatalf("view should not show the localhost callback note: %q", rendered)
	}
	if !strings.Contains(rendered, "paste the one-time code shown in the") ||
		!strings.Contains(rendered, "browser here.") {
		t.Fatalf("view missing browser code guidance: %q", rendered)
	}
}

func TestPromptSubmissionBlockedWhileExplicitOAuthLoginStarting(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.login = loginState{
		Provider: "openai-codex",
	}
	m.textInput.SetValue("hello")

	updated, _ := m.handleEnter()
	m = updated.(*model)

	if m.streaming {
		t.Fatal("prompt should not start while oauth login start is in flight")
	}
	if got := m.textInput.Value(); got != "hello" {
		t.Fatalf("textInput.Value() = %q, want preserved prompt", got)
	}
	if got := m.currentPromptFooter(); got != "login in progress; wait for completion or press Esc to cancel" {
		t.Fatalf("currentPromptFooter() = %q", got)
	}
}

func TestNonLoginSlashBlockedWhileExplicitOAuthLoginWaiting(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.login = loginState{
		Provider: "openai-codex",
		FlowID:   "flow-1",
		Waiting:  true,
	}

	updated, _ := m.submitSlashCommand("/model openai-responses:gpt-5.4", false)
	m = updated.(*model)

	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "note  model") {
		t.Fatalf("model slash should not execute while explicit login is pending: %q", rendered)
	}
	if got := m.currentPromptFooter(); got != "login in progress; only /login is available until completion or Esc" {
		t.Fatalf("currentPromptFooter() = %q", got)
	}
}

func TestOAuthModelSelectionStartsLoginInsteadOfOpenAISecretFlow(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/model openai-responses:gpt-5-codex")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("expected login start command")
	}
	updated, _ = m.Update(cmd())
	m = updated.(*model)

	if m.login.Active {
		t.Fatal("expected login overlay to close after URL is ready")
	}
	if m.auth.Active {
		t.Fatal("oauth-backed model selection should not start provider secret auth")
	}
	if got := m.login.PendingModel; got != "openai-responses:gpt-5-codex" {
		t.Fatalf("login.PendingModel = %q", got)
	}
	if !m.login.Waiting {
		t.Fatal("expected background login wait state")
	}
}

func TestOpenAICodexWaitSuccessShowsConfirmationBeforeRefreshingAuthStatus(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend
	m.login = loginState{
		Provider: "openai-codex",
		FlowID:   "flow-1",
		Waiting:  true,
	}
	accountID := "acct-test"

	updated, cmd := m.Update(waitOpenAICodexLoginMsg{
		Response: rpc.AuthLoginOpenAICodexWaitResponse{
			Status: rpc.OAuthProviderStatus{
				Provider:  "openai-codex",
				LoggedIn:  true,
				AccountID: &accountID,
			},
		},
	})
	m = updated.(*model)

	if backend.authStatusCalls != 0 {
		t.Fatalf("auth status should not be fetched synchronously, got %d calls", backend.authStatusCalls)
	}
	if cmd == nil {
		t.Fatal("expected background auth status refresh command")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "ChatGPT subscription login complete") {
		t.Fatalf("transcript missing immediate login confirmation: %q", rendered)
	}
	if strings.Contains(rendered, "acct-test") || strings.Contains(rendered, "account:") {
		t.Fatalf("transcript should not expose account id after login: %q", rendered)
	}
	if m.login.Waiting || m.login.FlowID != "" {
		t.Fatalf("login state should be cleared after success: %#v", m.login)
	}

	msg := cmd()
	if _, ok := msg.(authStatusLoadedMsg); !ok {
		t.Fatalf("auth refresh command returned %T, want authStatusLoadedMsg", msg)
	}
	if backend.authStatusCalls != 1 {
		t.Fatalf("auth status should be fetched in the background, got %d calls", backend.authStatusCalls)
	}
}

func TestOpenAICodexWaitSuccessShowsConfirmationInVisibleViewWithoutExtraInput(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/login openai-codex")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("expected login start command")
	}
	updated, _ = m.Update(cmd())
	m = updated.(*model)

	updated, _ = m.Update(waitOpenAICodexLoginMsg{
		Response: rpc.AuthLoginOpenAICodexWaitResponse{
			Status: rpc.OAuthProviderStatus{
				Provider: "openai-codex",
				LoggedIn: true,
			},
		},
	})
	m = updated.(*model)

	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "ChatGPT subscription login complete") {
		t.Fatalf("visible view missing login confirmation without extra input: %q", rendered)
	}
}

func TestPlainOpenAICodexLoginSelectsDefaultOAuthModelOnSuccess(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/login openai-codex")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("expected login start command")
	}
	updated, _ = m.Update(cmd())
	m = updated.(*model)

	updated, _ = m.Update(waitOpenAICodexLoginMsg{
		Response: rpc.AuthLoginOpenAICodexWaitResponse{
			Status: rpc.OAuthProviderStatus{
				Provider: "openai-codex",
				LoggedIn: true,
			},
		},
	})
	m = updated.(*model)

	if got := m.options.Model; got != "openai-responses:gpt-5-codex" {
		t.Fatalf("options.Model = %q, want %q", got, "openai-responses:gpt-5-codex")
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "model set to gpt-5-codex | oauth") {
		t.Fatalf("transcript missing oauth default model selection: %q", rendered)
	}
}

func TestOpenAICodexWaitSuccessForcesViewportToBottom(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()
	for i := 0; i < 30; i++ {
		m.transcript.WriteLine(fmt.Sprintf("line %02d", i))
	}
	m.login = loginState{
		Provider: "openai-codex",
		FlowID:   "flow-1",
		Waiting:  true,
	}
	m.refreshViewport()
	m.viewport.GotoTop()

	updated, _ := m.Update(waitOpenAICodexLoginMsg{
		Response: rpc.AuthLoginOpenAICodexWaitResponse{
			Status: rpc.OAuthProviderStatus{
				Provider: "openai-codex",
				LoggedIn: true,
			},
		},
	})
	m = updated.(*model)

	if m.viewport.YOffset == 0 {
		t.Fatal("login success should force viewport back to bottom")
	}
	rendered := stripANSI(m.View())
	if !strings.Contains(rendered, "ChatGPT subscription login complete") {
		t.Fatalf("visible view missing login confirmation after forced follow: %q", rendered)
	}
}

func TestWaitOpenAICodexLoginWaitsUntilDoneWithoutPolling(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	readyAt := time.Now().Add(120 * time.Millisecond)
	backend.oauthWaitDoneFn = func() bool {
		return time.Now().After(readyAt)
	}

	cmd := waitOpenAICodexLogin(backend, "flow-1")
	resultCh := make(chan tea.Msg, 1)
	go func() {
		resultCh <- cmd()
	}()

	select {
	case msg := <-resultCh:
		waitMsg, ok := msg.(waitOpenAICodexLoginMsg)
		if !ok {
			t.Fatalf("wait command returned %T, want waitOpenAICodexLoginMsg", msg)
		}
		if waitMsg.Err != nil {
			t.Fatalf("wait command returned unexpected error: %v", waitMsg.Err)
		}
		if !waitMsg.Response.Status.LoggedIn {
			t.Fatalf("wait command returned incomplete login status: %#v", waitMsg.Response)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("wait command did not complete after login became ready")
	}
}

func TestWaitOpenAICodexLoginUsesLongInteractiveTimeout(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	var remaining time.Duration
	backend.waitOpenAICodexFn = func(
		ctx context.Context,
		flowID string,
	) (rpc.AuthLoginOpenAICodexWaitResponse, error) {
		if flowID != "flow-1" {
			t.Fatalf("flowID = %q, want flow-1", flowID)
		}
		deadline, ok := ctx.Deadline()
		if !ok {
			t.Fatal("wait context missing deadline")
		}
		remaining = time.Until(deadline)
		return rpc.AuthLoginOpenAICodexWaitResponse{
			Status: rpc.OAuthProviderStatus{
				Provider: "openai-codex",
				LoggedIn: true,
			},
		}, nil
	}

	msg := waitOpenAICodexLogin(backend, "flow-1")()
	if _, ok := msg.(waitOpenAICodexLoginMsg); !ok {
		t.Fatalf("wait command returned %T, want waitOpenAICodexLoginMsg", msg)
	}
	if remaining < 10*time.Minute {
		t.Fatalf("wait timeout = %s, want at least 10m", remaining)
	}
}

func TestPromptSubmissionCanPasteOAuthCodeWhileLoginIsWaiting(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.login = loginState{
		Provider:     "openai-codex",
		FlowID:       "flow-1",
		Waiting:      true,
		PendingModel: "openai-responses:gpt-5.4-chatgpt",
	}
	m.textInput.SetValue("code-123")

	updated, cmd := m.handleEnter()
	m = updated.(*model)
	if cmd == nil {
		t.Fatal("expected raw pasted code to submit login completion")
	}
	updated, _ = m.Update(cmd())
	m = updated.(*model)

	if m.streaming {
		t.Fatal("prompt should not start while oauth login is pending")
	}
	if got := m.textInput.Value(); got != "" {
		t.Fatalf("textInput.Value() = %q, want cleared input", got)
	}
	if m.login.Waiting || m.login.FlowID != "" {
		t.Fatalf("login state should clear after manual completion: %#v", m.login)
	}
	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "ChatGPT subscription login complete") {
		t.Fatalf("transcript missing manual login confirmation: %q", rendered)
	}
}

func TestNonLoginSlashBlockedWhileOAuthLoginPendingForModel(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()
	m.login = loginState{
		Provider:     "openai-codex",
		FlowID:       "flow-1",
		Waiting:      true,
		PendingModel: "openai-responses:gpt-5.4-chatgpt",
	}

	updated, _ := m.submitSlashCommand("/model openai-responses:gpt-5.4", false)
	m = updated.(*model)

	rendered := stripANSI(m.transcript.Render())
	if strings.Contains(rendered, "note  model") {
		t.Fatalf("model slash should not execute while login is pending: %q", rendered)
	}
	if got := m.currentPromptFooter(); got != "login in progress; only /login is available until completion or Esc" {
		t.Fatalf("currentPromptFooter() = %q", got)
	}
}

func TestTraceCommandPersistsMode(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	m := newTestModel()
	m.options.Backend = newStubBackend()

	_ = sendKey(sendRunes(m, "/trace local"), tea.KeyMsg{Type: tea.KeyEnter})

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"trace_mode": "local"`) {
		t.Fatalf("config.json missing trace mode: %q", string(data))
	}
}

func TestTraceCommandBlocksLogfireUntilSetupIsReady(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	backend.logfireStatus = rpc.TraceLogfireStatusResponse{
		Installed:             false,
		CredentialsConfigured: false,
	}
	m := newTestModelWithBackend(backend)

	_ = sendKey(sendRunes(m, "/trace logfire"), tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"Logfire tracing is not ready yet.",
		"Install Logfire first so the `logfire` command is available.",
		"Install it with: pip install logfire",
		"Then run: logfire auth",
		"Then run: logfire projects use <project>",
		"Retry: /trace logfire",
		"Until then, use: /trace local",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("trace instructions missing %q in %q", want, rendered)
		}
	}

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err == nil && strings.Contains(string(data), `"trace_mode": "logfire"`) {
		t.Fatalf("config.json unexpectedly persisted broken logfire mode: %q", string(data))
	}
	if err != nil && !os.IsNotExist(err) {
		t.Fatalf("ReadFile() returned unexpected error: %v", err)
	}
}

func TestTraceCommandPersistsLogfireWhenSetupIsReady(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	backend.logfireStatus = rpc.TraceLogfireStatusResponse{
		Installed:             true,
		CredentialsConfigured: true,
	}
	m := newTestModelWithBackend(backend)

	_ = sendKey(sendRunes(m, "/trace logfire"), tea.KeyMsg{Type: tea.KeyEnter})

	data, err := os.ReadFile(home + "/.jaca/config.json")
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if !strings.Contains(string(data), `"trace_mode": "logfire"`) {
		t.Fatalf("config.json missing trace mode: %q", string(data))
	}
}

func TestTraceCommandShowsAuthStepsWhenLogfireNeedsLogin(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	backend := newStubBackend()
	backend.logfireStatus = rpc.TraceLogfireStatusResponse{
		Installed:             true,
		CredentialsConfigured: false,
	}
	m := newTestModelWithBackend(backend)

	_ = sendKey(sendRunes(m, "/trace logfire"), tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"Logfire tracing is not ready yet.",
		"Authenticate with Logfire: logfire auth",
		"Select a Logfire project: logfire projects use <project>",
		"Retry: /trace logfire",
		"Until then, use: /trace local",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("trace auth instructions missing %q in %q", want, rendered)
		}
	}
	if strings.Contains(rendered, "Install it with: pip install logfire") {
		t.Fatalf("did not expect install instructions when logfire is already installed: %q", rendered)
	}
}

func TestLoginOpenAIShowsAuthFileInstructions(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")
	authPath := filepath.Join(os.TempDir(), "jaca-auth.json")

	m := newTestModel()
	m.options.Backend = newStubBackend()

	m = sendRunes(m, "/login openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	for _, want := range []string{
		"Add your OpenAI API key to:",
		authPath,
		"Paste this into the empty file:",
		`"OPENAI_API_KEY": "..."`,
		"Save the file, then retry your prompt.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("auth instructions missing %q in %q", want, rendered)
		}
	}
	if m.auth.Active {
		t.Fatalf("auth flow should stay inactive: %#v", m.auth)
	}
}

func TestLoginAnthropicShowsAuthFileInstructions(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("ANTHROPIC_API_KEY", "")

	backend := newStubBackend()
	backend.localSecretStore = rpc.LocalSecretStoreStatus{
		Available:     true,
		FileStorePath: filepath.Join(home, ".jaca", "auth.json"),
	}
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/login anthropic")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "Add your Anthropic API key to:") {
		t.Fatalf("transcript missing anthropic auth note: %q", rendered)
	}
	if !strings.Contains(rendered, filepath.Join(home, ".jaca", "auth.json")) {
		t.Fatalf("transcript missing auth file path: %q", rendered)
	}
}

func TestPromptRequiringAuthStaysInComposer(t *testing.T) {
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

	if got := m.textInput.Value(); got != "run go tests" {
		t.Fatalf("textInput.Value() = %q, want original prompt restored", got)
	}
	if m.streaming {
		t.Fatal("missing auth should not start the run automatically")
	}
}

func TestModelWithoutCredentialsShowsAuthFileNote(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model openai-responses:gpt-5.4-mini")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "Add your OpenAI API key to:") {
		t.Fatalf("transcript missing auth note after model selection: %q", rendered)
	}
	if got := m.promptHistory; len(got) != 1 || got[0] != "/model openai-responses:gpt-5.4-mini" {
		t.Fatalf("promptHistory = %#v, want only the non-secret model command", got)
	}
}

func TestLoginStatusCommandRendersProviderSources(t *testing.T) {
	backend := newStubBackend()
	accountID := "acct-test"
	backend.authStatuses["anthropic"] = rpc.AuthProviderStatus{
		Provider:   "anthropic",
		Configured: true,
		Source:     "file",
		EnvKey:     "ANTHROPIC_API_KEY",
	}
	backend.oauthStatuses["openai-codex"] = rpc.OAuthProviderStatus{
		Provider:  "openai-codex",
		LoggedIn:  true,
		AccountID: &accountID,
	}

	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/login status")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "anthropic: configured (file)") {
		t.Fatalf("transcript missing anthropic auth status: %q", rendered)
	}
	if !strings.Contains(rendered, "openai-codex: logged in") {
		t.Fatalf("transcript missing oauth auth status: %q", rendered)
	}
	if strings.Contains(rendered, "acct-test") {
		t.Fatalf("auth status should not expose account id: %q", rendered)
	}
}

func TestLoginClearCommandCallsBackend(t *testing.T) {
	backend := newStubBackend()
	backend.authStatuses["openai"] = rpc.AuthProviderStatus{
		Provider:   "openai",
		Configured: true,
		Source:     "file",
		EnvKey:     "OPENAI_API_KEY",
	}

	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/login clear openai")
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
		AppVersion:            "0.1.5",
		Model:                 "openai-responses:gpt-5.4",
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

func TestLoginSelectionDoesNotHandleAPIKeysInTUI(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/login openai")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	transcript := stripANSI(m.transcript.Render())
	if !strings.Contains(transcript, "Add your OpenAI API key to:") {
		t.Fatalf("transcript missing auth-file note: %q", transcript)
	}
	if backend.lastSetSecret.Provider != "" || backend.lastSetSecret.Secret != "" {
		t.Fatalf("backend lastSetSecret = %#v", backend.lastSetSecret)
	}
}

func TestModelSelectionWithoutCredentialsDoesNotPersistPendingChoice(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "")

	backend := newStubBackend()
	m := newTestModel()
	m.options.Backend = backend

	m = sendRunes(m, "/model openai-responses:gpt-5.4")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := m.options.Model; got != "openai-responses:gpt-5.4" {
		t.Fatalf("options.Model = %q, want unchanged current model", got)
	}
	if strings.Contains(stripANSI(m.transcript.Render()), "super-secret") {
		t.Fatalf("secret leaked into transcript: %q", stripANSI(m.transcript.Render()))
	}
	if backend.lastSetSecret.Provider != "" || backend.lastSetSecret.Secret != "" {
		t.Fatalf("backend lastSetSecret = %#v", backend.lastSetSecret)
	}
}
