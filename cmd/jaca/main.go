package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/app"
	"jaca/internal/jaca/config"
	"jaca/internal/jaca/rpc"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "jaca: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	config.ApplyToEnv(cfg)

	defaultModel := os.Getenv("JACA_MODEL")
	if defaultModel == "" {
		defaultModel = "ollama:kimi-k2:1t-cloud"
	}

	model := flag.String("model", defaultModel, "Model to use")
	workspaceRoot := flag.String("workspace-root", ".", "Workspace root directory")
	sessionsRoot := flag.String("sessions-root", "", "Sessions storage directory")
	thinking := flag.String("thinking", "", "Thinking level")
	backendCommandJSON := flag.String("backend-command-json", "", "JSON array command used to start the canonical headless backend")
	flag.Parse()

	backendCommand, err := parseBackendCommandJSON(*backendCommandJSON)
	if err != nil {
		return err
	}

	absWorkspace, err := filepath.Abs(*workspaceRoot)
	if err != nil {
		return err
	}
	resolvedSessionsRoot, err := resolveSessionsRoot(*sessionsRoot)
	if err != nil {
		return err
	}

	manager := rpc.NewManager(rpc.BackendConfig{
		Model:         *model,
		WorkspaceRoot: absWorkspace,
		SessionsRoot:  resolvedSessionsRoot,
		Command:       backendCommand,
		Env:           os.Environ(),
	})
	defer func() {
		_ = manager.Shutdown(context.Background())
	}()

	program := tea.NewProgram(
		app.New(app.Options{
			Model:         *model,
			WorkspaceRoot: absWorkspace,
			SessionsRoot:  resolvedSessionsRoot,
			Thinking:      normalizeThinking(*thinking),
			Backend:       manager,
		}),
		tea.WithAltScreen(),
		tea.WithMouseCellMotion(),
	)
	_, err = program.Run()
	return err
}

func resolveSessionsRoot(raw string) (string, error) {
	if raw == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		raw = filepath.Join(home, ".jaca", "sessions")
	}
	resolved, err := filepath.Abs(raw)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(resolved, 0o755); err != nil {
		return "", err
	}
	return resolved, nil
}

func normalizeThinking(raw string) string {
	switch raw {
	case "", "true", "false", "minimal", "low", "medium", "high", "xhigh":
		return raw
	default:
		return ""
	}
}

func parseBackendCommandJSON(raw string) ([]string, error) {
	if raw == "" {
		return nil, fmt.Errorf("missing --backend-command-json; launch via the installed jaca wrapper or pass an explicit backend command")
	}
	var command []string
	if err := json.Unmarshal([]byte(raw), &command); err != nil {
		return nil, fmt.Errorf("invalid --backend-command-json: %w", err)
	}
	if len(command) == 0 {
		return nil, fmt.Errorf("invalid --backend-command-json: command cannot be empty")
	}
	for _, part := range command {
		if part == "" {
			return nil, fmt.Errorf("invalid --backend-command-json: command parts cannot be empty")
		}
	}
	return command, nil
}
