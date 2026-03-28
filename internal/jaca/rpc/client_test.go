package rpc

import (
	"context"
	"os"
	"os/exec"
	"runtime"
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
