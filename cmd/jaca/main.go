package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

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
	if err := config.ApplyTraceModeToEnv(cfg["trace_mode"]); err != nil {
		return err
	}

	defaultModel := resolveDefaultModel(cfg)

	model := flag.String("model", defaultModel, "Model to use")
	workspaceRoot := flag.String("workspace-root", ".", "Workspace root directory")
	sessionsRoot := flag.String("sessions-root", "", "Sessions storage directory")
	sessionID := flag.String("session-id", "", "Existing session id to resume")
	sessionName := flag.String("session-name", "", "Resolved human session name for the resumed session")
	forkedFromSessionID := flag.String("forked-from-session-id", "", "Parent session id for a forked session")
	forkedFromSessionName := flag.String("forked-from-session-name", "", "Resolved human parent session name for a forked session")
	thinking := flag.String("thinking", "", "Thinking level")
	backendCommandJSON := flag.String("backend-command-json", "", "JSON array command used to start the canonical headless backend")
	appVersion := flag.String("app-version", "", "Installed JACA package version")
	availableUpdateVersion := flag.String("available-update-version", "", "Newer published JACA version, if one is available")
	availableUpdateCommandJSON := flag.String("available-update-command-json", "", "JSON array command used to upgrade to the newer published JACA version")
	flag.Parse()
	if *model == "" {
		return fmt.Errorf("missing model; launch via the installed jaca wrapper or set JACA_MODEL/default_model")
	}

	backendCommand, err := parseBackendCommandJSON(*backendCommandJSON)
	if err != nil {
		return err
	}
	availableUpdateCommand, err := parseOptionalCommandJSON(*availableUpdateCommandJSON)
	if err != nil {
		return err
	}
	updateNotice, err := buildUpdateNotice(*availableUpdateVersion, availableUpdateCommand)
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
			AppVersion:            *appVersion,
			AvailableUpdate:       updateNotice,
			Model:                 *model,
			WorkspaceRoot:         absWorkspace,
			SessionsRoot:          resolvedSessionsRoot,
			SessionID:             *sessionID,
			SessionName:           *sessionName,
			ForkedFromSessionID:   *forkedFromSessionID,
			ForkedFromSessionName: *forkedFromSessionName,
			Thinking:              normalizeThinking(*thinking),
			Backend:               manager,
		}),
		tea.WithAltScreen(),
		tea.WithMouseCellMotion(),
	)
	finalModel, err := program.Run()
	if err != nil {
		return err
	}
	if reporter, ok := finalModel.(interface{ ExitAction() *app.ExternalAction }); ok {
		action := reporter.ExitAction()
		if action != nil {
			return runExternalAction(action)
		}
	}
	return nil
}

func resolveDefaultModel(cfg map[string]string) string {
	defaultModel := os.Getenv("JACA_MODEL")
	if defaultModel != "" {
		return defaultModel
	}
	if value := cfg["default_model"]; value != "" {
		return value
	}
	return ""
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

func parseOptionalCommandJSON(raw string) ([]string, error) {
	if raw == "" {
		return nil, nil
	}
	var command []string
	if err := json.Unmarshal([]byte(raw), &command); err != nil {
		return nil, fmt.Errorf("invalid command JSON: %w", err)
	}
	if len(command) == 0 {
		return nil, fmt.Errorf("invalid command JSON: command cannot be empty")
	}
	for _, part := range command {
		if part == "" {
			return nil, fmt.Errorf("invalid command JSON: command parts cannot be empty")
		}
	}
	return command, nil
}

func buildUpdateNotice(latestVersion string, command []string) (*app.UpdateNotice, error) {
	latestVersion = strings.TrimSpace(latestVersion)
	switch {
	case latestVersion == "" && len(command) == 0:
		return nil, nil
	case latestVersion == "":
		return nil, fmt.Errorf("missing --available-update-version")
	case len(command) == 0:
		return nil, fmt.Errorf("missing --available-update-command-json")
	default:
		return &app.UpdateNotice{
			LatestVersion: latestVersion,
			Command:       append([]string{}, command...),
		}, nil
	}
}

func runExternalAction(action *app.ExternalAction) error {
	if action == nil {
		return nil
	}
	switch action.Kind {
	case app.ExternalActionUpdate:
		if len(action.Command) == 0 {
			return fmt.Errorf("missing external update command")
		}
		cmdline := strings.Join(action.Command, " ")
		fmt.Printf(
			"Updating JACA: %s -> %s\n",
			action.CurrentVersion,
			action.LatestVersion,
		)
		fmt.Printf("Running `%s`...\n\n", cmdline)
		cmd := exec.Command(action.Command[0], action.Command[1:]...)
		cmd.Stdin = os.Stdin
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("external update failed: %w", err)
		}
		fmt.Println("\nUpdate ran successfully. Restart JACA.")
		return nil
	default:
		return fmt.Errorf("unknown external action: %s", action.Kind)
	}
}
