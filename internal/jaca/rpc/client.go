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

func (m *Manager) PollOpenAICodexLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginOpenAICodexPollResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginOpenAICodexPollResponse{}, err
	}
	return client.PollOpenAICodexLogin(ctx, flowID)
}

func (m *Manager) StartGitHubCopilotLogin(
	ctx context.Context,
	enterpriseDomain string,
) (AuthLoginGitHubCopilotStartResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginGitHubCopilotStartResponse{}, err
	}
	return client.StartGitHubCopilotLogin(ctx, enterpriseDomain)
}

func (m *Manager) PollGitHubCopilotLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginGitHubCopilotPollResponse, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return AuthLoginGitHubCopilotPollResponse{}, err
	}
	return client.PollGitHubCopilotLogin(ctx, flowID)
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
		if err != nil && !strings.Contains(err.Error(), "signal: killed") && !strings.Contains(err.Error(), "signal: interrupt") {
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

func (c *Client) PollOpenAICodexLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginOpenAICodexPollResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginOpenAICodexPollResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_openai_codex.poll",
		Payload: AuthLoginOpenAICodexPollPayload{FlowID: flowID},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginOpenAICodexPollResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginOpenAICodexPollResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginOpenAICodexPollResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginOpenAICodexPollResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginOpenAICodexPollResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginOpenAICodexPollResponse{}, fmt.Errorf("unexpected envelope for auth.login_openai_codex.poll: %T", line)
	}
}

func (c *Client) StartGitHubCopilotLogin(
	ctx context.Context,
	enterpriseDomain string,
) (AuthLoginGitHubCopilotStartResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginGitHubCopilotStartResponse{}, err
	}
	defer cleanup()
	payload := AuthLoginGitHubCopilotStartPayload{}
	if strings.TrimSpace(enterpriseDomain) != "" {
		payload.EnterpriseDomain = &enterpriseDomain
	}
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_github_copilot.start",
		Payload: payload,
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginGitHubCopilotStartResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginGitHubCopilotStartResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginGitHubCopilotStartResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginGitHubCopilotStartResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginGitHubCopilotStartResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginGitHubCopilotStartResponse{}, fmt.Errorf("unexpected envelope for auth.login_github_copilot.start: %T", line)
	}
}

func (c *Client) PollGitHubCopilotLogin(
	ctx context.Context,
	flowID string,
) (AuthLoginGitHubCopilotPollResponse, error) {
	requestID := c.nextRequestID()
	waiter, cleanup, err := c.registerWaiter(requestID)
	if err != nil {
		return AuthLoginGitHubCopilotPollResponse{}, err
	}
	defer cleanup()
	c.writeMu.Lock()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "auth.login_github_copilot.poll",
		Payload: AuthLoginGitHubCopilotPollPayload{FlowID: flowID},
	}); err != nil {
		c.writeMu.Unlock()
		return AuthLoginGitHubCopilotPollResponse{}, err
	}
	c.writeMu.Unlock()
	line, err := c.awaitEnvelope(ctx, waiter)
	if err != nil {
		return AuthLoginGitHubCopilotPollResponse{}, err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response AuthLoginGitHubCopilotPollResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return AuthLoginGitHubCopilotPollResponse{}, err
		}
		return response, nil
	case ErrorEnvelope:
		return AuthLoginGitHubCopilotPollResponse{}, fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return AuthLoginGitHubCopilotPollResponse{}, fmt.Errorf("unexpected envelope for auth.login_github_copilot.poll: %T", line)
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
