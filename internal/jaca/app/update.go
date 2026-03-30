package app

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
)

const updateCheckURL = "https://pypi.org/pypi/just-another-coding-agent/json"

type updatePromptState struct {
	Active         bool
	CurrentVersion string
	LatestVersion  string
	Command        []string
	Selected       int
	Running        bool
}

type updateCheckMsg struct {
	LatestVersion string
	Command       []string
	Err           error
}

type updateRunMsg struct {
	Command []string
	Err     error
}

func fetchUpdatePrompt(currentVersion string, command []string) tea.Cmd {
	if currentVersion == "" || len(command) == 0 {
		return nil
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
		defer cancel()

		latestVersion, err := fetchLatestVersion(ctx)
		if err != nil {
			return updateCheckMsg{Err: err}
		}
		newer, ok := isNewerReleaseVersion(currentVersion, latestVersion)
		if !ok || !newer {
			return updateCheckMsg{}
		}
		return updateCheckMsg{LatestVersion: latestVersion, Command: command}
	}
}

func fetchLatestVersion(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, updateCheckURL, nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("update check failed: %s", resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	var payload struct {
		Info struct {
			Version string `json:"version"`
		} `json:"info"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return "", err
	}
	return strings.TrimSpace(payload.Info.Version), nil
}

func isNewerReleaseVersion(current string, latest string) (bool, bool) {
	currentParts, ok := parseReleaseVersion(current)
	if !ok {
		return false, false
	}
	latestParts, ok := parseReleaseVersion(latest)
	if !ok {
		return false, false
	}
	for i := range currentParts {
		if latestParts[i] > currentParts[i] {
			return true, true
		}
		if latestParts[i] < currentParts[i] {
			return false, true
		}
	}
	return false, true
}

func parseReleaseVersion(raw string) ([3]int, bool) {
	var parts [3]int
	clean := strings.TrimSpace(strings.TrimPrefix(raw, "v"))
	if clean == "" || strings.Contains(clean, "-") || strings.Contains(clean, "+") {
		return parts, false
	}
	chunks := strings.Split(clean, ".")
	if len(chunks) != 3 {
		return parts, false
	}
	for i, chunk := range chunks {
		value, err := strconv.Atoi(chunk)
		if err != nil || value < 0 {
			return parts, false
		}
		parts[i] = value
	}
	return parts, true
}

func (u updatePromptState) commandText() string {
	return strings.Join(u.Command, " ")
}

func (u updatePromptState) options() []string {
	return []string{
		"Update now",
		"Skip",
		fmt.Sprintf("Skip until %s", u.LatestVersion),
	}
}

func runInstalledUpdate(command []string) tea.Cmd {
	if len(command) == 0 {
		return nil
	}
	return func() tea.Msg {
		cmd := exec.Command(command[0], command[1:]...)
		err := cmd.Run()
		return updateRunMsg{Command: append([]string(nil), command...), Err: err}
	}
}

func saveSkippedUpdateVersion(version string) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	if version == "" {
		delete(cfg, "update_skip_version")
	} else {
		cfg["update_skip_version"] = version
	}
	return config.Save(cfg)
}
