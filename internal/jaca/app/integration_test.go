package app

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/rpc"
)

const helperEnv = "JACA_GO_TUI_TEST_HELPER"

type helperRequest struct {
	ID      string          `json:"id"`
	Command string          `json:"command"`
	Payload json.RawMessage `json:"payload"`
}

func TestModelRunsAgainstRealRPCBackendProcess(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("OPENAI_API_KEY", "test-key")
	configDir := filepath.Join(home, ".jaca")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(configDir, "config.json"),
		[]byte("{\n  \"default_provider\": \"openai\",\n  \"default_model\": \"openai:test-model\"\n}\n"),
		0o600,
	); err != nil {
		t.Fatalf("write config: %v", err)
	}

	workspaceRoot := t.TempDir()
	sessionsRoot := t.TempDir()
	backend := rpc.NewManager(rpc.BackendConfig{
		Model:         "openai:test-model",
		WorkspaceRoot: workspaceRoot,
		SessionsRoot:  sessionsRoot,
		Command:       []string{os.Args[0], "-test.run=TestGoTUIRPCBackendHelperProcess", "--"},
		Env:           append(os.Environ(), helperEnv+"=1"),
	})
	t.Cleanup(func() {
		_ = backend.Shutdown(context.Background())
	})

	m := New(Options{
		Model:         "openai:test-model",
		WorkspaceRoot: workspaceRoot,
		SessionsRoot:  sessionsRoot,
		Thinking:      "high",
		Backend:       backend,
	}).(*model)
	m.viewport = newViewport()
	m.viewport.Width = 80
	m.viewport.Height = 10
	m.width = 80
	m.height = 14
	m.visibleZones = 3

	m.workspaceTrust = &rpc.WorkspaceTrustStatusResponse{
		Trusted:     true,
		TrustTarget: workspaceRoot,
	}
	m.textInput.SetValue("ship it")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = runIntegrationCmd(updated.(*model), cmd)

	for msg := range m.asyncCh {
		m.Update(msg)
	}
	if m.liveFlushScheduled {
		m.Update(liveFlushMsg{})
	}

	rendered := stripANSI(m.transcript.Render())
	if !strings.Contains(rendered, "ship it") {
		t.Fatalf("transcript missing user prompt: %q", rendered)
	}
	if !strings.Contains(rendered, "loaded project instructions: AGENTS.md") {
		t.Fatalf("transcript missing instructions note: %q", rendered)
	}
	if !strings.Contains(rendered, "read  README.md") || !strings.Contains(rendered, "12ms") {
		t.Fatalf("transcript missing tool activity: %q", rendered)
	}
	if !strings.Contains(rendered, "python - <<'PY'  ok  500ms") {
		t.Fatalf("transcript missing completed tool row after live update: %q", rendered)
	}
	if strings.Contains(rendered, "command still running") || strings.Contains(rendered, "streaming output line") {
		t.Fatalf("transcript kept live tool update state after completion: %q", rendered)
	}
	if !strings.Contains(rendered, "final answer") {
		t.Fatalf("transcript missing completed assistant output: %q", rendered)
	}
	if !strings.Contains(rendered, "shipped from helper backend") {
		t.Fatalf("transcript missing completed markdown content: %q", rendered)
	}
	if m.sessionID != "sess-integration" {
		t.Fatalf("sessionID = %q, want %q", m.sessionID, "sess-integration")
	}
	if m.phase != PhaseCompleted && m.phase != PhaseIdle {
		t.Fatalf("phase = %q, want %q or %q", m.phase, PhaseCompleted, PhaseIdle)
	}
}

func runIntegrationCmd(m *model, cmd tea.Cmd) *model {
	if cmd == nil {
		return m
	}
	msg := cmd()
	if msg == nil {
		return m
	}
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, child := range batch {
			m = runIntegrationCmd(m, child)
		}
		return m
	}
	updated, next := m.Update(msg)
	return runIntegrationCmd(updated.(*model), next)
}

func TestGoTUIRPCBackendHelperProcess(t *testing.T) {
	if os.Getenv(helperEnv) != "1" {
		return
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 16*1024), 2*1024*1024)
	encoder := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var request helperRequest
		if err := json.Unmarshal(scanner.Bytes(), &request); err != nil {
			fmt.Fprintf(os.Stderr, "helper decode request: %v\n", err)
			os.Exit(1)
		}
		switch request.Command {
		case "model.catalog":
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"providers": []map[string]any{
						{
							"provider":         "openai",
							"default_model_id": "openai-responses:gpt-5.4",
							"models": []map[string]any{
								{
									"model_id":    "openai-responses:gpt-5.4",
									"description": "OpenAI GPT-5.4 Responses",
								},
							},
						},
					},
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode model.catalog response: %v\n", err)
				os.Exit(1)
			}
		case "auth.status":
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"providers": []map[string]any{
						{
							"provider":   "openai",
							"configured": true,
							"source":     "env",
							"env_key":    "OPENAI_API_KEY",
						},
					},
					"local_secret_store": map[string]any{
						"available":       true,
						"message":         nil,
						"file_store_path": "",
					},
					"oauth_providers": []map[string]any{},
					"mcp_servers":     []map[string]any{},
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode auth.status response: %v\n", err)
				os.Exit(1)
			}
		case "workspace.trust_status":
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"trusted":      true,
					"trust_target": "/workspace",
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode workspace.trust_status response: %v\n", err)
				os.Exit(1)
			}
		case "workspace.trust_accept":
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"trusted":      true,
					"trust_target": "/workspace",
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode workspace.trust_accept response: %v\n", err)
				os.Exit(1)
			}
		case "session.create":
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"session_id": "sess-integration",
					"project_docs": []map[string]any{
						{
							"path":      "/workspace/AGENTS.md",
							"filename":  "AGENTS.md",
							"truncated": false,
						},
					},
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode session.create response: %v\n", err)
				os.Exit(1)
			}
		case "run.start":
			var payload rpc.RunStartPayload
			if err := json.Unmarshal(request.Payload, &payload); err != nil {
				fmt.Fprintf(os.Stderr, "helper decode run.start payload: %v\n", err)
				os.Exit(1)
			}
			if payload.SessionID != "sess-integration" {
				fmt.Fprintf(os.Stderr, "unexpected session id: %q\n", payload.SessionID)
				os.Exit(1)
			}
			if payload.Prompt != "ship it" {
				fmt.Fprintf(os.Stderr, "unexpected prompt: %q\n", payload.Prompt)
				os.Exit(1)
			}
			thinking, ok := payload.Thinking.(string)
			if !ok || thinking != "high" {
				fmt.Fprintf(os.Stderr, "unexpected thinking payload: %#v\n", payload.Thinking)
				os.Exit(1)
			}
			duration := 12
			updateDuration := 250
			finalDuration := 500
			events := []map[string]any{
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":   "assistant_text_delta",
						"run_id": "run-helper",
						"delta":  "reading the repo",
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":         "tool_call_started",
						"run_id":       "run-helper",
						"tool_call_id": "tool-1",
						"tool_name":    "read",
						"args": map[string]any{
							"path": "README.md",
						},
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":         "tool_call_succeeded",
						"run_id":       "run-helper",
						"tool_call_id": "tool-1",
						"tool_name":    "read",
						"result":       "README snapshot",
						"activity": map[string]any{
							"title":       "read README.md",
							"duration_ms": duration,
						},
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":         "tool_call_started",
						"run_id":       "run-helper",
						"tool_call_id": "tool-2",
						"tool_name":    "shell",
						"args": map[string]any{
							"command": "python - <<'PY'",
						},
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":         "tool_call_updated",
						"run_id":       "run-helper",
						"tool_call_id": "tool-2",
						"tool_name":    "shell",
						"partial_result": map[string]any{
							"output": "streaming output line\n",
						},
						"activity": map[string]any{
							"title":       "shell python - <<'PY'",
							"summary":     "command still running",
							"duration_ms": updateDuration,
						},
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":         "tool_call_succeeded",
						"run_id":       "run-helper",
						"tool_call_id": "tool-2",
						"tool_name":    "shell",
						"result": map[string]any{
							"exit_code": 0,
							"output":    "bash complete\n",
						},
						"activity": map[string]any{
							"title":       "shell python - <<'PY'",
							"summary":     "command exited 0",
							"duration_ms": finalDuration,
						},
					},
				},
				{
					"type": "rpc_event",
					"id":   request.ID,
					"event": map[string]any{
						"type":        "run_succeeded",
						"run_id":      "run-helper",
						"output_text": "final answer\n\n- shipped from helper backend",
					},
				},
			}
			for _, event := range events {
				if err := encoder.Encode(event); err != nil {
					fmt.Fprintf(os.Stderr, "helper encode run event: %v\n", err)
					os.Exit(1)
				}
			}
			if err := encoder.Encode(map[string]any{
				"type": "rpc_response",
				"id":   request.ID,
				"response": map[string]any{
					"session_id": payload.SessionID,
				},
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode run.start response: %v\n", err)
				os.Exit(1)
			}
		default:
			if err := encoder.Encode(map[string]any{
				"type":       "rpc_error",
				"id":         request.ID,
				"error_type": "unknown_command",
				"message":    fmt.Sprintf("unsupported helper command: %s", request.Command),
			}); err != nil {
				fmt.Fprintf(os.Stderr, "helper encode error: %v\n", err)
				os.Exit(1)
			}
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "helper scanner error: %v\n", err)
		os.Exit(1)
	}
	os.Exit(0)
}
