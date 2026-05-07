package rpc

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestClientClosePrefersGracefulEOF(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/close.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		"import os, pathlib, sys; ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY']); marker = pathlib.Path(os.environ['JACA_RPC_HELPER_MARKER']); ready.write_text('ready'); sys.stdin.read(); marker.write_text('eof')",
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := client.Close(ctx); err != nil {
		t.Fatalf("Close() returned error: %v", err)
	}

	data, err := os.ReadFile(markerPath)
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if string(data) != "eof" {
		t.Fatalf("helper marker = %q, want %q", data, "eof")
	}
}

func TestClientInterruptSendsSIGINT(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/interrupt.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		"import os, pathlib, signal; ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY']); marker = pathlib.Path(os.environ['JACA_RPC_HELPER_MARKER']);\n\ndef handle(sig, frame):\n    marker.write_text('interrupt');\n    raise SystemExit(0)\n\nsignal.signal(signal.SIGINT, handle)\nready.write_text('ready')\nsignal.pause()",
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := client.Interrupt(ctx); err != nil {
		t.Fatalf("Interrupt() returned error: %v", err)
	}

	data, err := os.ReadFile(markerPath)
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if string(data) != "interrupt" {
		t.Fatalf("helper marker = %q, want %q", data, "interrupt")
	}
}

func TestClientCloseDoesNotHangWhenDescendantKeepsPipesOpen(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/descendant-close.txt"
	readyPath := tmpDir + "/ready.txt"
	childPIDPath := tmpDir + "/child.pid"
	cfg := BackendConfig{
		Model:         "test-model",
		WorkspaceRoot: t.TempDir(),
		SessionsRoot:  t.TempDir(),
		Command: []string{
			"python3",
			"-c",
			"import os, pathlib, subprocess, sys; ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY']); marker = pathlib.Path(os.environ['JACA_RPC_HELPER_MARKER']); child_pid = pathlib.Path(os.environ['JACA_RPC_HELPER_CHILD_PID']); ready.write_text('ready'); sys.stdin.read(); child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); child_pid.write_text(str(child.pid)); marker.write_text('spawned')",
		},
		Env: append(
			os.Environ(),
			"JACA_RPC_HELPER_MARKER="+markerPath,
			"JACA_RPC_HELPER_READY="+readyPath,
			"JACA_RPC_HELPER_CHILD_PID="+childPIDPath,
		),
	}

	client, err := StartClient(cfg)
	if err != nil {
		t.Fatalf("StartClient() returned error: %v", err)
	}
	waitForHelperReady(t, readyPath)
	t.Cleanup(func() {
		if data, err := os.ReadFile(childPIDPath); err == nil {
			pid, convErr := strconv.Atoi(strings.TrimSpace(string(data)))
			if convErr == nil && pid > 0 {
				if process, findErr := os.FindProcess(pid); findErr == nil {
					_ = process.Kill()
				}
			}
		}
	})

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	done := make(chan error, 1)
	go func() {
		done <- client.Close(ctx)
	}()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Close() returned error: %v", err)
		}
	case <-time.After(1500 * time.Millisecond):
		t.Fatal("Close() hung while descendant kept backend pipes open")
	}

	data, err := os.ReadFile(markerPath)
	if err != nil {
		t.Fatalf("ReadFile() returned error: %v", err)
	}
	if string(data) != "spawned" {
		t.Fatalf("helper marker = %q, want %q", data, "spawned")
	}
}

func TestClientModelCatalog(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/catalog.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		"import pathlib, os, sys; ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY']); ready.write_text('ready');\nfor line in sys.stdin:\n    sys.stdout.write('{\"type\":\"rpc_response\",\"id\":\"go-1\",\"response\":{\"providers\":[{\"provider\":\"ollama\",\"default_model_id\":\"ollama:kimi-k2:1t-cloud\",\"models\":[{\"model_id\":\"ollama:kimi-k2:1t-cloud\",\"description\":\"Current default Kimi K2\"}]}]}}\\n'); sys.stdout.flush(); break",
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	response, err := client.ModelCatalog(ctx)
	if err != nil {
		t.Fatalf("ModelCatalog() returned error: %v", err)
	}
	if len(response.Providers) != 1 {
		t.Fatalf("len(Providers) = %d, want 1", len(response.Providers))
	}
	if response.Providers[0].DefaultModelID != "ollama:kimi-k2:1t-cloud" {
		t.Fatalf("DefaultModelID = %q, want ollama:kimi-k2:1t-cloud", response.Providers[0].DefaultModelID)
	}
}

func TestClientModelCatalogMatchesResponsesByRequestID(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/catalog-order.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		`import pathlib, os, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
count = 0
for _line in sys.stdin:
    count += 1
    if count == 1:
        sys.stdout.write('{"type":"rpc_response","id":"go-2","response":{"providers":[{"provider":"openai","default_model_id":"openai-responses:gpt-5.4","models":[{"model_id":"openai-responses:gpt-5.4","description":"GPT-5.4 Responses"}]}]}}' + "\n")
        sys.stdout.write('{"type":"rpc_response","id":"go-1","response":{"providers":[{"provider":"ollama","default_model_id":"ollama:kimi-k2:1t-cloud","models":[{"model_id":"ollama:kimi-k2:1t-cloud","description":"Kimi"}]}]}}' + "\n")
        sys.stdout.flush()
    elif count == 2:
        break`,
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	first, err := client.ModelCatalog(ctx)
	if err != nil {
		t.Fatalf("first ModelCatalog() returned error: %v", err)
	}
	if got := first.Providers[0].DefaultModelID; got != "ollama:kimi-k2:1t-cloud" {
		t.Fatalf("first DefaultModelID = %q, want %q", got, "ollama:kimi-k2:1t-cloud")
	}

	second, err := client.ModelCatalog(ctx)
	if err != nil {
		t.Fatalf("second ModelCatalog() returned error: %v", err)
	}
	if got := second.Providers[0].DefaultModelID; got != "openai-responses:gpt-5.4" {
		t.Fatalf("second DefaultModelID = %q, want %q", got, "openai-responses:gpt-5.4")
	}
}

func TestClientAuthStatusWhileStreamRunIsActive(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/stream-auth.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		`import json, os, pathlib, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] == "run.start":
        sys.stdout.write(json.dumps({
            "type": "rpc_event",
            "id": request["id"],
            "event": {"type": "run_started", "run_id": "run-1"},
        }) + "\n")
        sys.stdout.flush()
    elif request["command"] == "auth.status":
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": request["id"],
            "response": {
                "providers": [{
                    "provider": "openai",
                    "configured": True,
                    "secret_configured": True,
                    "requires_secret": True,
                    "source": "env",
                    "env_key": "OPENAI_API_KEY",
                    "reason": "ok",
                }],
                "local_secret_store": {
                    "available": True,
                    "message": None,
                    "file_store_path": "",
                },
            },
        }) + "\n")
        sys.stdout.write(json.dumps({
            "type": "rpc_event",
            "id": "go-1",
            "event": {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"},
        }) + "\n")
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": "go-1",
            "response": {"session_id": "sess-1"},
        }) + "\n")
        sys.stdout.flush()
        break`,
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	runStarted := make(chan struct{}, 1)
	runDone := make(chan error, 1)
	go func() {
		runDone <- client.StreamRun(ctx, "sess-1", "ship it", "", "", func(event RunEvent) error {
			if event.Type == "run_started" {
				runStarted <- struct{}{}
			}
			return nil
		})
	}()

	select {
	case <-runStarted:
	case err := <-runDone:
		t.Fatalf("StreamRun() finished before run_started: %v", err)
	case <-ctx.Done():
		t.Fatal("timed out waiting for run_started")
	}

	var (
		authResp AuthStatusResponse
		authErr  error
		wg       sync.WaitGroup
	)
	wg.Add(1)
	go func() {
		defer wg.Done()
		authResp, authErr = client.AuthStatus(ctx)
	}()
	wg.Wait()

	if authErr != nil {
		t.Fatalf("AuthStatus() returned error: %v", authErr)
	}
	if len(authResp.Providers) != 1 || authResp.Providers[0].Provider != "openai" {
		t.Fatalf("AuthStatus() providers = %#v, want openai", authResp.Providers)
	}

	if err := <-runDone; err != nil {
		t.Fatalf("StreamRun() returned error: %v", err)
	}
}

func TestClientStreamRunIncludesOnboardingModeWhenRequested(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		tmpDir+"/mode.txt",
		readyPath,
		`import json, os, pathlib, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] != "run.start":
        continue
    payload = request["payload"]
    assert payload["mode"] == "onboarding", payload
    sys.stdout.write(json.dumps({
        "type": "rpc_event",
        "id": request["id"],
        "event": {"type": "run_started", "run_id": "run-1"},
    }) + "\n")
    sys.stdout.write(json.dumps({
        "type": "rpc_event",
        "id": request["id"],
        "event": {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"},
    }) + "\n")
    sys.stdout.write(json.dumps({
        "type": "rpc_response",
        "id": request["id"],
        "response": {"session_id": payload["session_id"]},
    }) + "\n")
    sys.stdout.flush()
    break`,
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := client.StreamRun(
		ctx,
		"sess-1",
		"onboard me",
		"",
		"onboarding",
		func(event RunEvent) error { return nil },
	); err != nil {
		t.Fatalf("StreamRun() returned error: %v", err)
	}
}

func TestClientAuthStatusWhileOpenAICodexWaitIsActive(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/wait-auth.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		`import json, os, pathlib, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
wait_id = None
pending_auth_request = None
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] == "auth.login_openai_codex.wait":
        wait_id = request["id"]
        if pending_auth_request is None:
            continue
        request = pending_auth_request
    elif request["command"] == "auth.status":
        if wait_id is None:
            pending_auth_request = request
            continue
    else:
        continue
    sys.stdout.write(json.dumps({
        "type": "rpc_response",
        "id": request["id"],
        "response": {
            "providers": [{
                "provider": "openai",
                "configured": True,
                "secret_configured": True,
                "requires_secret": True,
                "source": "env",
                "env_key": "OPENAI_API_KEY",
                "reason": "ok",
            }],
            "local_secret_store": {
                "available": True,
                "message": None,
                "file_store_path": "",
            },
            "oauth_providers": [{
                "provider": "openai-codex",
                "logged_in": True,
                "account_id": "acct-123",
                "expires_at": 1760000000000,
            }],
        },
    }) + "\n")
    sys.stdout.write(json.dumps({
        "type": "rpc_response",
        "id": wait_id,
        "response": {
            "status": {
                "provider": "openai-codex",
                "logged_in": True,
                "account_id": "acct-123",
                "expires_at": 1760000000000,
            }
        },
    }) + "\n")
    sys.stdout.flush()
    break`,
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	waitDone := make(chan struct{})
	waitErr := make(chan error, 1)
	go func() {
		defer close(waitDone)
		resp, err := client.WaitOpenAICodexLogin(ctx, "flow-1")
		if err != nil {
			waitErr <- err
			return
		}
		if got := resp.Status.Provider; got != "openai-codex" {
			waitErr <- fmt.Errorf("provider = %q, want %q", got, "openai-codex")
			return
		}
	}()

	authResp, err := client.AuthStatus(ctx)
	if err != nil {
		t.Fatalf("AuthStatus() returned error: %v", err)
	}
	if len(authResp.OAuthProviders) != 1 || authResp.OAuthProviders[0].Provider != "openai-codex" {
		t.Fatalf("OAuthProviders = %#v, want openai-codex", authResp.OAuthProviders)
	}

	select {
	case <-waitDone:
	case err := <-waitErr:
		t.Fatalf("WaitOpenAICodexLogin() returned error: %v", err)
	case <-ctx.Done():
		t.Fatal("timed out waiting for login wait completion")
	}
}

func TestClientEnqueueRunWhileStreamRunIsActive(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/stream-enqueue.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		`import json, os, pathlib, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
run_id = None
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] == "run.start":
        run_id = request["id"]
        sys.stdout.write(json.dumps({
            "type": "rpc_event",
            "id": run_id,
            "event": {"type": "run_started", "run_id": "run-1"},
        }) + "\n")
        sys.stdout.flush()
    elif request["command"] == "run.enqueue":
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": request["id"],
            "response": {"session_id": "sess-1", "queued_count": 1},
        }) + "\n")
        sys.stdout.write(json.dumps({
            "type": "rpc_event",
            "id": run_id,
            "event": {"type": "run_succeeded", "run_id": "run-1", "output_text": "done"},
        }) + "\n")
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": run_id,
            "response": {"session_id": "sess-1"},
        }) + "\n")
        sys.stdout.flush()
        break`,
	)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	runStarted := make(chan struct{}, 1)
	runDone := make(chan error, 1)
	go func() {
		runDone <- client.StreamRun(ctx, "sess-1", "ship it", "", "", func(event RunEvent) error {
			if event.Type == "run_started" {
				runStarted <- struct{}{}
			}
			return nil
		})
	}()

	select {
	case <-runStarted:
	case err := <-runDone:
		t.Fatalf("StreamRun() finished before run_started: %v", err)
	case <-ctx.Done():
		t.Fatal("timed out waiting for run_started")
	}

	resp, err := client.EnqueueRun(ctx, "sess-1", "follow up", "later")
	if err != nil {
		t.Fatalf("EnqueueRun() returned error: %v", err)
	}
	if resp.SessionID != "sess-1" {
		t.Fatalf("SessionID = %q, want %q", resp.SessionID, "sess-1")
	}
	if resp.QueuedCount != 1 {
		t.Fatalf("QueuedCount = %d, want 1", resp.QueuedCount)
	}

	if err := <-runDone; err != nil {
		t.Fatalf("StreamRun() returned error: %v", err)
	}
}

func TestClientEnqueueRunDoesNotStarveWhenStreamSinkIsSlow(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based helper is unix-only")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}

	tmpDir := t.TempDir()
	markerPath := tmpDir + "/stream-slow-enqueue.txt"
	readyPath := tmpDir + "/ready.txt"
	client := startPythonHelperClient(
		t,
		markerPath,
		readyPath,
		`import json, os, pathlib, sys
ready = pathlib.Path(os.environ['JACA_RPC_HELPER_READY'])
ready.write_text('ready')
run_id = None
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] == "run.start":
        run_id = request["id"]
        for idx in range(80):
            sys.stdout.write(json.dumps({
                "type": "rpc_event",
                "id": run_id,
                "event": {"type": "assistant_text_delta", "delta": f"chunk-{idx}"},
            }) + "\n")
        sys.stdout.flush()
    elif request["command"] == "run.enqueue":
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": request["id"],
            "response": {"session_id": "sess-1", "queued_count": 1},
        }) + "\n")
        sys.stdout.write(json.dumps({
            "type": "rpc_response",
            "id": run_id,
            "response": {"session_id": "sess-1"},
        }) + "\n")
        sys.stdout.flush()
        break`,
	)

	runCtx, runCancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer runCancel()
	readyForEnqueue := make(chan struct{}, 1)
	runDone := make(chan error, 1)
	go func() {
		seen := 0
		runDone <- client.StreamRun(runCtx, "sess-1", "ship it", "", "", func(event RunEvent) error {
			if event.Type == "assistant_text_delta" {
				seen++
				if seen == 1 {
					readyForEnqueue <- struct{}{}
					time.Sleep(250 * time.Millisecond)
				}
			}
			return nil
		})
	}()

	select {
	case <-readyForEnqueue:
	case err := <-runDone:
		t.Fatalf("StreamRun() finished before delta: %v", err)
	case <-runCtx.Done():
		t.Fatal("timed out waiting for first delta")
	}

	enqueueCtx, enqueueCancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer enqueueCancel()
	resp, err := client.EnqueueRun(enqueueCtx, "sess-1", "follow up", "later")
	if err != nil {
		t.Fatalf("EnqueueRun() returned error: %v", err)
	}
	if resp.QueuedCount != 1 {
		t.Fatalf("QueuedCount = %d, want 1", resp.QueuedCount)
	}

	if err := <-runDone; err != nil {
		t.Fatalf("StreamRun() returned error: %v", err)
	}
}

func startPythonHelperClient(t *testing.T, markerPath string, readyPath string, script string) *Client {
	t.Helper()

	cfg := BackendConfig{
		Model:         "test-model",
		WorkspaceRoot: t.TempDir(),
		SessionsRoot:  t.TempDir(),
		Command:       []string{"python3", "-c", script},
		Env: append(
			os.Environ(),
			"JACA_RPC_HELPER_MARKER="+markerPath,
			"JACA_RPC_HELPER_READY="+readyPath,
		),
	}

	client, err := StartClient(cfg)
	if err != nil {
		t.Fatalf("StartClient() returned error: %v", err)
	}
	waitForHelperReady(t, readyPath)
	return client
}

func waitForHelperReady(t *testing.T, readyPath string) {
	t.Helper()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(readyPath); err == nil {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("helper did not write ready marker: %s", readyPath)
}
