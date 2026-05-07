package rpc

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type BackendConfig struct {
	Model         string
	WorkspaceRoot string
	SessionsRoot  string
	Command       []string
	Env           []string
}

type Manager struct {
	mu     sync.Mutex
	cfg    BackendConfig
	client *Client
}

func NewManager(cfg BackendConfig) *Manager {
	return &Manager{cfg: cfg}
}

func (m *Manager) SetModel(model string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.cfg.Model = model
}

func (m *Manager) SetEnv(env []string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.cfg.Env = append([]string{}, env...)
}

func (m *Manager) Restart(ctx context.Context) error {
	m.mu.Lock()
	client := m.client
	m.client = nil
	m.mu.Unlock()
	if client != nil {
		return client.Close(ctx)
	}
	return nil
}

func (m *Manager) Shutdown(ctx context.Context) error {
	return m.Restart(ctx)
}

func (m *Manager) Interrupt(ctx context.Context) error {
	m.mu.Lock()
	client := m.client
	m.client = nil
	m.mu.Unlock()
	if client != nil {
		return client.Interrupt(ctx)
	}
	return nil
}

func (m *Manager) InterruptRun(
	ctx context.Context,
	sessionID string,
	promoteQueuedSteer bool,
) (RunInterruptResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return RunInterruptResponse{}, err
	}
	return client.InterruptRun(ctx, sessionID, promoteQueuedSteer)
}

func (m *Manager) SubmitOnboarding(
	ctx context.Context,
	sessionID string,
	attemptID string,
	selectedIndex int,
) (OnboardingSubmitResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return OnboardingSubmitResponse{}, err
	}
	return client.SubmitOnboarding(ctx, sessionID, attemptID, selectedIndex)
}

func (m *Manager) ensureStartedLocked() (*Client, error) {
	if m.client != nil {
		return m.client, nil
	}
	client, err := StartClient(m.cfg)
	if err != nil {
		return nil, err
	}
	m.client = client
	return client, nil
}

func (m *Manager) CreateSession(ctx context.Context) (SessionCreateResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return SessionCreateResponse{}, err
	}
	return client.CreateSession(ctx)
}

func (m *Manager) WorkspaceTrustStatus(ctx context.Context) (WorkspaceTrustStatusResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return WorkspaceTrustStatusResponse{}, err
	}
	return client.WorkspaceTrustStatus(ctx)
}

func (m *Manager) AcceptWorkspaceTrust(ctx context.Context) (WorkspaceTrustAcceptResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return WorkspaceTrustAcceptResponse{}, err
	}
	return client.AcceptWorkspaceTrust(ctx)
}

func (m *Manager) PrepareAuthFile(ctx context.Context, provider string) (AuthPrepareFileResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthPrepareFileResponse{}, err
	}
	return client.PrepareAuthFile(ctx, provider)
}

func (m *Manager) SetSessionName(ctx context.Context, sessionID string, name string) (SessionNameResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return SessionNameResponse{}, err
	}
	return client.SetSessionName(ctx, sessionID, name)
}

func (m *Manager) SessionPreview(ctx context.Context, sessionID string) (SessionPreviewResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return SessionPreviewResponse{}, err
	}
	return client.SessionPreview(ctx, sessionID)
}

func (m *Manager) WorkspaceProjectDocs(ctx context.Context) (WorkspaceProjectDocsResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return WorkspaceProjectDocsResponse{}, err
	}
	return client.WorkspaceProjectDocs(ctx)
}

func (m *Manager) CompactSession(ctx context.Context, sessionID string) (SessionCompactResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return SessionCompactResponse{}, err
	}
	return client.CompactSession(ctx, sessionID)
}

func (m *Manager) ModelCatalog(ctx context.Context) (ModelCatalogResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return ModelCatalogResponse{}, err
	}
	return client.ModelCatalog(ctx)
}

func (m *Manager) AuthStatus(ctx context.Context) (AuthStatusResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthStatusResponse{}, err
	}
	return client.AuthStatus(ctx)
}

func (m *Manager) TraceLogfireStatus(ctx context.Context) (TraceLogfireStatusResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return TraceLogfireStatusResponse{}, err
	}
	return client.TraceLogfireStatus(ctx)
}

func (m *Manager) PermissionGet(ctx context.Context, sessionID string) (PermissionGetResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return PermissionGetResponse{}, err
	}
	return client.PermissionGet(ctx, sessionID)
}

func (m *Manager) PermissionSet(
	ctx context.Context,
	sessionID string,
	sandboxPolicy *SandboxPolicy,
	approvalPolicy *ApprovalPolicy,
) (PermissionSetResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return PermissionSetResponse{}, err
	}
	return client.PermissionSet(ctx, sessionID, sandboxPolicy, approvalPolicy)
}

func (m *Manager) ApprovalSubmit(
	ctx context.Context,
	sessionID string,
	decision ApprovalDecision,
) (ApprovalSubmitResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return ApprovalSubmitResponse{}, err
	}
	return client.ApprovalSubmit(ctx, sessionID, decision)
}

func (m *Manager) StartOpenAICodexLogin(ctx context.Context) (AuthLoginOpenAICodexStartResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginOpenAICodexStartResponse{}, err
	}
	return client.StartOpenAICodexLogin(ctx)
}

func (m *Manager) CompleteOpenAICodexLogin(
	ctx context.Context,
	flowID string,
	callbackOrCode string,
) (AuthLoginOpenAICodexCompleteResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginOpenAICodexCompleteResponse{}, err
	}
	return client.CompleteOpenAICodexLogin(ctx, flowID, callbackOrCode)
}

func (m *Manager) WaitOpenAICodexLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginOpenAICodexWaitResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginOpenAICodexWaitResponse{}, err
	}
	return client.WaitOpenAICodexLogin(ctx, flowID)
}

func (m *Manager) SetProviderSecret(
	ctx context.Context,
	provider string,
	secret string,
	storage string,
) (AuthSetResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthSetResponse{}, err
	}
	return client.SetProviderSecret(ctx, provider, secret, storage)
}

func (m *Manager) ClearProviderSecret(
	ctx context.Context,
	provider string,
) (AuthClearResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthClearResponse{}, err
	}
	return client.ClearProviderSecret(ctx, provider)
}

func (m *Manager) StreamRun(
	ctx context.Context,
	sessionID string,
	prompt string,
	thinking string,
	sink func(RunEvent) error,
) error {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return err
	}
	return client.StreamRun(ctx, sessionID, prompt, thinking, sink)
}

func (m *Manager) EnqueueRun(
	ctx context.Context,
	sessionID string,
	prompt string,
	mode string,
) (RunEnqueueResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return RunEnqueueResponse{}, err
	}
	return client.EnqueueRun(ctx, sessionID, prompt, mode)
}

type Client struct {
	cmd              *exec.Cmd
	stdin            io.WriteCloser
	stderr           bytes.Buffer
	writeMu          sync.Mutex
	routeMu          sync.Mutex
	requestID        atomic.Uint64
	readResults      chan readResult
	waiters          map[string]chan readResult
	pendingEnvelopes map[string][]readResult
	terminalErr      error
}

type readResult struct {
	value any
	err   error
}

func StartClient(cfg BackendConfig) (*Client, error) {
	if len(cfg.Command) == 0 {
		return nil, errors.New("missing backend command")
	}
	args := append([]string{}, cfg.Command[1:]...)
	args = append(
		args,
		"--headless",
		"--model", cfg.Model,
		"--workspace-root", cfg.WorkspaceRoot,
		"--sessions-root", cfg.SessionsRoot,
	)
	cmd := exec.Command(cfg.Command[0], args...)
	cmd.Env = cfg.Env
	// The backend command can be a launcher like `uv` that spawns a
	// descendant Python process. On shutdown, descendants may briefly keep the
	// stdout/stderr pipe file descriptors open even after the tracked process
	// exits, which can otherwise make Cmd.Wait() block indefinitely in
	// awaitGoroutines. Bound that pipe-drain wait so client shutdown stays
	// finite.
	cmd.WaitDelay = 500 * time.Millisecond
	// Kernel-enforced parent-death propagation on Linux: when this Go TUI
	// exits for any reason the backend is guaranteed to receive SIGTERM
	// from the kernel, closing the zombie-backend class of failures.
	setParentDeathSignal(cmd)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	client := &Client{
		cmd:              cmd,
		stdin:            stdin,
		readResults:      make(chan readResult, 32),
		waiters:          map[string]chan readResult{},
		pendingEnvelopes: map[string][]readResult{},
	}
	cmd.Stderr = &client.stderr
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	go client.readLoop(stdout)
	go client.dispatchLoop()
	return client, nil
}

func (c *Client) readLoop(stdout io.Reader) {
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 16*1024), 2*1024*1024)
	for scanner.Scan() {
		value, err := decodeEnvelope(scanner.Bytes())
		c.readResults <- readResult{value: value, err: err}
		if err != nil {
			close(c.readResults)
			return
		}
	}
	if err := scanner.Err(); err != nil {
		c.readResults <- readResult{err: err}
		close(c.readResults)
		return
	}
	stderr := strings.TrimSpace(c.stderr.String())
	if stderr == "" {
		stderr = "backend process exited unexpectedly"
	}
	c.readResults <- readResult{err: errors.New(stderr)}
	close(c.readResults)
}

func (c *Client) Close(ctx context.Context) error {
	if c.cmd.Process == nil {
		return nil
	}
	_ = c.stdin.Close()
	if err := c.waitGracefulExit(ctx, 350*time.Millisecond); err == nil {
		return nil
	}
	if err := c.cmd.Process.Kill(); err != nil && !strings.Contains(err.Error(), "process already finished") {
		return err
	}
	return c.waitExit(context.Background())
}

func (c *Client) Interrupt(ctx context.Context) error {
	if c.cmd.Process == nil {
		return nil
	}
	if err := c.cmd.Process.Signal(os.Interrupt); err != nil {
		if strings.Contains(err.Error(), "process already finished") {
			return nil
		}
		return c.Close(ctx)
	}
	if err := c.waitGracefulExit(ctx, 1200*time.Millisecond); err == nil {
		return nil
	}
	if err := c.cmd.Process.Kill(); err != nil && !strings.Contains(err.Error(), "process already finished") {
		return err
	}
	return c.waitExit(context.Background())
}

func (c *Client) waitGracefulExit(ctx context.Context, fallback time.Duration) error {
	waitCtx := ctx
	cancel := func() {}
	if _, ok := ctx.Deadline(); !ok {
		waitCtx, cancel = context.WithTimeout(ctx, fallback)
	}
	defer cancel()
	return c.waitExit(waitCtx)
}

func (c *Client) waitExit(ctx context.Context) error {
	waitCh := make(chan error, 1)
	go func() {
		waitCh <- c.cmd.Wait()
	}()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-waitCh:
		if err != nil &&
			!errors.Is(err, exec.ErrWaitDelay) &&
			!strings.Contains(err.Error(), "signal: killed") &&
			!strings.Contains(err.Error(), "signal: interrupt") {
			return err
		}
		return nil
	}
}

func (c *Client) nextRequestID() string {
	id := c.requestID.Add(1)
	return fmt.Sprintf("go-%d", id)
}

func (c *Client) dispatchLoop() {
	for result := range c.readResults {
		if result.err != nil {
			c.failAllWaiters(result.err)
			return
		}
		requestID := envelopeRequestID(result.value)
		if requestID == "" {
			c.failAllWaiters(errors.New("backend emitted envelope without request id"))
			return
		}
		c.routeMu.Lock()
		waiter, ok := c.waiters[requestID]
		if !ok {
			c.pendingEnvelopes[requestID] = append(c.pendingEnvelopes[requestID], result)
			c.routeMu.Unlock()
			continue
		}
		c.routeMu.Unlock()
		waiter <- result
	}
	c.failAllWaiters(errors.New("backend reader stopped unexpectedly"))
}

func (c *Client) failAllWaiters(err error) {
	c.routeMu.Lock()
	if c.terminalErr == nil {
		c.terminalErr = err
	}
	waiters := c.waiters
	c.waiters = map[string]chan readResult{}
	c.routeMu.Unlock()
	for _, waiter := range waiters {
		waiter <- readResult{err: err}
		close(waiter)
	}
}

func (c *Client) registerWaiter(requestID string) (chan readResult, func(), error) {
	c.routeMu.Lock()
	defer c.routeMu.Unlock()
	if c.terminalErr != nil {
		return nil, nil, c.terminalErr
	}
	waiter := make(chan readResult, 32)
	if pending := c.pendingEnvelopes[requestID]; len(pending) > 0 {
		for _, result := range pending {
			waiter <- result
		}
		delete(c.pendingEnvelopes, requestID)
	}
	c.waiters[requestID] = waiter
	cleanup := func() {
		c.routeMu.Lock()
		waiter, ok := c.waiters[requestID]
		if ok {
			delete(c.waiters, requestID)
			close(waiter)
		}
		c.routeMu.Unlock()
	}
	return waiter, cleanup, nil
}

func (c *Client) awaitEnvelope(
	ctx context.Context,
	waiter <-chan readResult,
) (any, error) {
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case result, ok := <-waiter:
		if !ok {
			c.routeMu.Lock()
			err := c.terminalErr
			c.routeMu.Unlock()
			if err == nil {
				err = errors.New("backend reader stopped unexpectedly")
			}
			return nil, err
		}
		if result.err != nil {
			return nil, result.err
		}
		return result.value, nil
	}
}

func (c *Client) CreateSession(ctx context.Context) (SessionCreateResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return SessionCreateResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.create",
		Payload: SessionCreatePayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return SessionCreateResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return SessionCreateResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response SessionCreateResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return SessionCreateResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return SessionCreateResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return SessionCreateResponse{}, fmt.Errorf("unexpected envelope for session.create: %T", line)
	}
}

func (c *Client) WorkspaceTrustStatus(ctx context.Context) (WorkspaceTrustStatusResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return WorkspaceTrustStatusResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "workspace.trust_status",
		Payload: WorkspaceTrustStatusPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return WorkspaceTrustStatusResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return WorkspaceTrustStatusResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response WorkspaceTrustStatusResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return WorkspaceTrustStatusResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return WorkspaceTrustStatusResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return WorkspaceTrustStatusResponse{}, fmt.Errorf("unexpected envelope for workspace.trust_status: %T", line)
	}
}

func (c *Client) AcceptWorkspaceTrust(ctx context.Context) (WorkspaceTrustAcceptResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return WorkspaceTrustAcceptResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "workspace.trust_accept",
		Payload: WorkspaceTrustAcceptPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return WorkspaceTrustAcceptResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return WorkspaceTrustAcceptResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response WorkspaceTrustAcceptResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return WorkspaceTrustAcceptResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return WorkspaceTrustAcceptResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return WorkspaceTrustAcceptResponse{}, fmt.Errorf("unexpected envelope for workspace.trust_accept: %T", line)
	}
}

func (c *Client) SetSessionName(ctx context.Context, sessionID string, name string) (SessionNameResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return SessionNameResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.name",
		Payload: SessionNamePayload{
			SessionID: sessionID,
			Name:      name,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return SessionNameResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return SessionNameResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response SessionNameResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return SessionNameResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return SessionNameResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return SessionNameResponse{}, fmt.Errorf("unexpected envelope for session.name: %T", line)
	}
}

func (c *Client) SessionPreview(ctx context.Context, sessionID string) (SessionPreviewResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return SessionPreviewResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.preview",
		Payload: SessionPreviewPayload{SessionID: sessionID},
	}); err != nil {
		c.writeMu.Unlock()
		return SessionPreviewResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return SessionPreviewResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response SessionPreviewResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return SessionPreviewResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return SessionPreviewResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return SessionPreviewResponse{}, fmt.Errorf("unexpected envelope for session.preview: %T", line)
	}
}

func (c *Client) WorkspaceProjectDocs(ctx context.Context) (WorkspaceProjectDocsResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return WorkspaceProjectDocsResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "workspace.project_docs",
		Payload: WorkspaceProjectDocsPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return WorkspaceProjectDocsResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return WorkspaceProjectDocsResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response WorkspaceProjectDocsResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return WorkspaceProjectDocsResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return WorkspaceProjectDocsResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return WorkspaceProjectDocsResponse{}, fmt.Errorf("unexpected envelope for workspace.project_docs: %T", line)
	}
}

func (c *Client) CompactSession(ctx context.Context, sessionID string) (SessionCompactResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return SessionCompactResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.compact",
		Payload: SessionCompactPayload{SessionID: sessionID},
	}); err != nil {
		c.writeMu.Unlock()
		return SessionCompactResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return SessionCompactResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response SessionCompactResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return SessionCompactResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return SessionCompactResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return SessionCompactResponse{}, fmt.Errorf("unexpected envelope for session.compact: %T", line)
	}
}

func (c *Client) ModelCatalog(ctx context.Context) (ModelCatalogResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return ModelCatalogResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "model.catalog",
		Payload: ModelCatalogPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return ModelCatalogResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return ModelCatalogResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response ModelCatalogResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return ModelCatalogResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return ModelCatalogResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return ModelCatalogResponse{}, fmt.Errorf("unexpected envelope for model.catalog: %T", line)
	}
}

func (c *Client) AuthStatus(ctx context.Context) (AuthStatusResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthStatusResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.status",
		Payload: AuthStatusPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthStatusResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthStatusResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthStatusResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthStatusResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthStatusResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthStatusResponse{}, fmt.Errorf("unexpected envelope for auth.status: %T", line)
	}
}

func (c *Client) TraceLogfireStatus(ctx context.Context) (TraceLogfireStatusResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return TraceLogfireStatusResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "trace.logfire_status",
		Payload: TraceLogfireStatusPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return TraceLogfireStatusResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return TraceLogfireStatusResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response TraceLogfireStatusResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return TraceLogfireStatusResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return TraceLogfireStatusResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return TraceLogfireStatusResponse{}, fmt.Errorf("unexpected envelope for trace.logfire_status: %T", line)
	}
}

func (c *Client) PermissionGet(ctx context.Context, sessionID string) (PermissionGetResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return PermissionGetResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "permission.get",
		Payload: PermissionGetPayload{SessionID: sessionID},
	}); err != nil {
		c.writeMu.Unlock()
		return PermissionGetResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return PermissionGetResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response PermissionGetResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return PermissionGetResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return PermissionGetResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return PermissionGetResponse{}, fmt.Errorf("unexpected envelope for permission.get: %T", line)
	}
}

func (c *Client) PermissionSet(
	ctx context.Context,
	sessionID string,
	sandboxPolicy *SandboxPolicy,
	approvalPolicy *ApprovalPolicy,
) (PermissionSetResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return PermissionSetResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "permission.set",
		Payload: PermissionSetPayload{
			SessionID:      sessionID,
			SandboxPolicy:  sandboxPolicy,
			ApprovalPolicy: approvalPolicy,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return PermissionSetResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return PermissionSetResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response PermissionSetResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return PermissionSetResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return PermissionSetResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return PermissionSetResponse{}, fmt.Errorf("unexpected envelope for permission.set: %T", line)
	}
}

func (c *Client) ApprovalSubmit(
	ctx context.Context,
	sessionID string,
	decision ApprovalDecision,
) (ApprovalSubmitResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return ApprovalSubmitResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "approval.submit",
		Payload: ApprovalSubmitPayload{
			SessionID: sessionID,
			Decision:  decision,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return ApprovalSubmitResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return ApprovalSubmitResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response ApprovalSubmitResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return ApprovalSubmitResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return ApprovalSubmitResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return ApprovalSubmitResponse{}, fmt.Errorf("unexpected envelope for approval.submit: %T", line)
	}
}

func (c *Client) StartOpenAICodexLogin(ctx context.Context) (AuthLoginOpenAICodexStartResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginOpenAICodexStartResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_openai_codex.start",
		Payload: AuthLoginOpenAICodexStartPayload{},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginOpenAICodexStartResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginOpenAICodexStartResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginOpenAICodexStartResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginOpenAICodexStartResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginOpenAICodexStartResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginOpenAICodexStartResponse{}, fmt.Errorf("unexpected envelope for auth.login_openai_codex.start: %T", line)
	}
}

func (c *Client) CompleteOpenAICodexLogin(
	ctx context.Context,
	flowID string,
	callbackOrCode string,
) (AuthLoginOpenAICodexCompleteResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginOpenAICodexCompleteResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_openai_codex.complete",
		Payload: AuthLoginOpenAICodexCompletePayload{
			FlowID:         flowID,
			CallbackOrCode: callbackOrCode,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginOpenAICodexCompleteResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginOpenAICodexCompleteResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginOpenAICodexCompleteResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginOpenAICodexCompleteResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginOpenAICodexCompleteResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginOpenAICodexCompleteResponse{}, fmt.Errorf("unexpected envelope for auth.login_openai_codex.complete: %T", line)
	}
}

func (c *Client) WaitOpenAICodexLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginOpenAICodexWaitResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginOpenAICodexWaitResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_openai_codex.wait",
		Payload: AuthLoginOpenAICodexWaitPayload{FlowID: flowID},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginOpenAICodexWaitResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginOpenAICodexWaitResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginOpenAICodexWaitResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginOpenAICodexWaitResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginOpenAICodexWaitResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginOpenAICodexWaitResponse{}, fmt.Errorf("unexpected envelope for auth.login_openai_codex.wait: %T", line)
	}
}

func (c *Client) PrepareAuthFile(
	ctx context.Context,
	provider string,
) (AuthPrepareFileResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthPrepareFileResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.prepare_file",
		Payload: AuthPrepareFilePayload{Provider: provider},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthPrepareFileResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthPrepareFileResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthPrepareFileResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthPrepareFileResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthPrepareFileResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthPrepareFileResponse{}, fmt.Errorf("unexpected envelope for auth.prepare_file: %T", line)
	}
}

func (c *Client) SetProviderSecret(
	ctx context.Context,
	provider string,
	secret string,
	storage string,
) (AuthSetResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthSetResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.set",
		Payload: AuthSetPayload{
			Provider: provider,
			Secret:   secret,
			Storage:  storage,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthSetResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthSetResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthSetResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthSetResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthSetResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthSetResponse{}, fmt.Errorf("unexpected envelope for auth.set: %T", line)
	}
}

func (c *Client) ClearProviderSecret(
	ctx context.Context,
	provider string,
) (AuthClearResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthClearResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.clear",
		Payload: AuthClearPayload{Provider: provider},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthClearResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthClearResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthClearResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthClearResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthClearResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthClearResponse{}, fmt.Errorf("unexpected envelope for auth.clear: %T", line)
	}
}

func (c *Client) StreamRun(
	ctx context.Context,
	sessionID string,
	prompt string,
	thinking string,
	sink func(RunEvent) error,
) error {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return err
	}
	defer cleanup()
	payload := RunStartPayload{
		SessionID: sessionID,
		Prompt:    prompt,
	}
	if thinking != "" {
		switch thinking {
		case "true":
			payload.Thinking = true
		case "false":
			payload.Thinking = false
		default:
			payload.Thinking = thinking
		}
	}
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "run.start",
		Payload: payload,
	}); err != nil {
		c.writeMu.Unlock()
		return err
	}
	c.writeMu.Unlock()
	for {
		line, err := c.awaitEnvelope(ctx, waiter)
		if err != nil {
			return err
		}
		switch envelope := line.(type) {
		case EventEnvelope:
			if err := sink(envelope.Event); err != nil {
				return err
			}
			continue
		case ResponseEnvelope:
			var response RunStartResponse
			if err := json.Unmarshal(envelope.Response, &response); err != nil {
				return err
			}
			if response.SessionID != sessionID {
				return fmt.Errorf(
					"unexpected session_id for run.start: %s",
					response.SessionID,
				)
			}
			return nil
		case ErrorEnvelope:
			return fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
		default:
			return fmt.Errorf("unexpected envelope for run.start: %T", line)
		}
	}
}

func (c *Client) EnqueueRun(
	ctx context.Context,
	sessionID string,
	prompt string,
	mode string,
) (RunEnqueueResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return RunEnqueueResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "run.enqueue",
		Payload: RunEnqueuePayload{
			SessionID: sessionID,
			Prompt:    prompt,
			Mode:      mode,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return RunEnqueueResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return RunEnqueueResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response RunEnqueueResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return RunEnqueueResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return RunEnqueueResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return RunEnqueueResponse{}, fmt.Errorf("unexpected envelope for run.enqueue: %T", line)
	}
}

func (c *Client) InterruptRun(
	ctx context.Context,
	sessionID string,
	promoteQueuedSteer bool,
) (RunInterruptResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return RunInterruptResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "run.interrupt",
		Payload: RunInterruptPayload{
			SessionID:          sessionID,
			PromoteQueuedSteer: promoteQueuedSteer,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return RunInterruptResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return RunInterruptResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response RunInterruptResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return RunInterruptResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return RunInterruptResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return RunInterruptResponse{}, fmt.Errorf("unexpected envelope for run.interrupt: %T", line)
	}
}

func (c *Client) SubmitOnboarding(
	ctx context.Context,
	sessionID string,
	attemptID string,
	selectedIndex int,
) (OnboardingSubmitResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return OnboardingSubmitResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "onboarding.submit",
		Payload: OnboardingSubmitPayload{
			SessionID:     sessionID,
			AttemptID:     attemptID,
			SelectedIndex: selectedIndex,
		},
	}); err != nil {
		c.writeMu.Unlock()
		return OnboardingSubmitResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return OnboardingSubmitResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response OnboardingSubmitResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return OnboardingSubmitResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return OnboardingSubmitResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return OnboardingSubmitResponse{}, fmt.Errorf("unexpected envelope for onboarding.submit: %T", line)
	}
}

func (c *Client) writeRequest(request Request) error {
	data, err := json.Marshal(request)
	if err != nil {
		return err
	}
	if _, err := c.stdin.Write(append(data, '\n')); err != nil {
		return err
	}
	return nil
}

func envelopeRequestID(envelope any) string {
	switch envelope := envelope.(type) {
	case ResponseEnvelope:
		return envelope.ID
	case EventEnvelope:
		return envelope.ID
	case ErrorEnvelope:
		if envelope.ID == nil {
			return ""
		}
		return *envelope.ID
	default:
		return ""
	}
}
