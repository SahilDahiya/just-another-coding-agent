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

func (m *Manager) CreateSession(ctx context.Context) (string, error) {
	m.mu.Lock()
	client, err := m.ensureStartedLocked()
	m.mu.Unlock()
	if err != nil {
		return "", err
	}
	return client.CreateSession(ctx)
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

type Client struct {
	cmd       *exec.Cmd
	stdin     io.WriteCloser
	scanner   *bufio.Scanner
	stderr    bytes.Buffer
	mu        sync.Mutex
	requestID atomic.Uint64
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
		cmd:   cmd,
		stdin: stdin,
	}
	cmd.Stderr = &client.stderr
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 16*1024), 2*1024*1024)
	client.scanner = scanner
	return client, nil
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

func (c *Client) CreateSession(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	requestID := c.nextRequestID()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.create",
		Payload: SessionCreatePayload{},
	}); err != nil {
		return "", err
	}
	line, err := c.readEnvelope(ctx)
	if err != nil {
		return "", err
	}
	switch envelope := line.(type) {
	case ResponseEnvelope:
		var response SessionCreateResponse
		if err := json.Unmarshal(envelope.Response, &response); err != nil {
			return "", err
		}
		return response.SessionID, nil
	case ErrorEnvelope:
		return "", fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
	default:
		return "", fmt.Errorf("unexpected envelope for session.create: %T", line)
	}
}

func (c *Client) CompactSession(ctx context.Context, sessionID string) (SessionCompactResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	requestID := c.nextRequestID()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "session.compact",
		Payload: SessionCompactPayload{SessionID: sessionID},
	}); err != nil {
		return SessionCompactResponse{}, err
	}
	line, err := c.readEnvelope(ctx)
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
	c.mu.Lock()
	defer c.mu.Unlock()
	requestID := c.nextRequestID()
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "model.catalog",
		Payload: ModelCatalogPayload{},
	}); err != nil {
		return ModelCatalogResponse{}, err
	}
	line, err := c.readEnvelope(ctx)
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

func (c *Client) StreamRun(
	ctx context.Context,
	sessionID string,
	prompt string,
	thinking string,
	sink func(RunEvent) error,
) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	requestID := c.nextRequestID()
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
	if err := c.writeRequest(Request{
		ID:      requestID,
		Command: "run.start",
		Payload: payload,
	}); err != nil {
		return err
	}
	for {
		line, err := c.readEnvelope(ctx)
		if err != nil {
			return err
		}
		switch envelope := line.(type) {
		case EventEnvelope:
			if err := sink(envelope.Event); err != nil {
				return err
			}
			if envelope.Event.Type == "run_succeeded" || envelope.Event.Type == "run_failed" {
				return nil
			}
		case ErrorEnvelope:
			return fmt.Errorf("%s: %s", envelope.ErrorType, envelope.Message)
		default:
			return fmt.Errorf("unexpected envelope for run.start: %T", line)
		}
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

func (c *Client) readEnvelope(ctx context.Context) (any, error) {
	type result struct {
		value any
		err   error
	}
	resultCh := make(chan result, 1)
	go func() {
		if c.scanner.Scan() {
			value, err := decodeEnvelope(c.scanner.Bytes())
			resultCh <- result{value: value, err: err}
			return
		}
		if err := c.scanner.Err(); err != nil {
			resultCh <- result{err: err}
			return
		}
		stderr := strings.TrimSpace(c.stderr.String())
		if stderr == "" {
			stderr = "backend process exited unexpectedly"
		}
		resultCh <- result{err: errors.New(stderr)}
	}()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case result := <-resultCh:
		return result.value, result.err
	}
}
