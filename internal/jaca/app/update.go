package app

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"jaca/internal/jaca/config"
)

const updateSnoozeDuration = 24 * time.Hour

type UpdateNotice struct {
	LatestVersion string
	Command       []string
}

type updateState struct {
	Active         bool
	Selected       int
	CurrentVersion string
	LatestVersion  string
	Command        []string
}

type ExternalActionKind string

const (
	ExternalActionUpdate ExternalActionKind = "update"
)

type ExternalAction struct {
	Kind           ExternalActionKind
	CurrentVersion string
	LatestVersion  string
	Command        []string
}

func initialUpdateState(options Options) updateState {
	if options.AvailableUpdate == nil {
		return updateState{}
	}
	cfg, err := config.Load()
	if err != nil {
		return updateState{}
	}
	if !shouldPromptForUpdate(cfg, options.AvailableUpdate.LatestVersion, time.Now()) {
		return updateState{}
	}
	return updateState{
		Active:         true,
		Selected:       1,
		CurrentVersion: options.AppVersion,
		LatestVersion:  options.AvailableUpdate.LatestVersion,
		Command:        append([]string{}, options.AvailableUpdate.Command...),
	}
}

func shouldPromptForUpdate(cfg map[string]string, latestVersion string, now time.Time) bool {
	latestVersion = strings.TrimSpace(latestVersion)
	if latestVersion == "" {
		return false
	}
	if strings.TrimSpace(cfg["update_skip_version"]) == latestVersion {
		return false
	}
	rawUntil := strings.TrimSpace(cfg["update_snooze_until"])
	if rawUntil == "" {
		return true
	}
	until, err := time.Parse(time.RFC3339, rawUntil)
	if err != nil {
		return true
	}
	return !now.Before(until)
}

func (m *model) updateTitle() string {
	return "Update available"
}

func (m *model) updateOptionLines() []string {
	return []string{
		"1. Update now",
		"2. Later",
		fmt.Sprintf("3. Skip %s", m.update.LatestVersion),
	}
}

func (m *model) updateHelpLines() []string {
	return []string{
		fmt.Sprintf("JACA %s -> %s", m.update.CurrentVersion, m.update.LatestVersion),
		"Update now exits JACA and runs the updater.",
		"Later snoozes this notice for 24 hours.",
		"Skip this version stays quiet until a newer release exists.",
		"Enter selects. Esc chooses Later.",
	}
}

func (m *model) handleUpdateKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		return m.completeUpdateSelection(1)
	case "up":
		if m.update.Selected > 0 {
			m.update.Selected--
			m.refreshViewport()
		}
		return m, nil
	case "down":
		if m.update.Selected < len(m.updateOptionLines())-1 {
			m.update.Selected++
			m.refreshViewport()
		}
		return m, nil
	case "1", "2", "3":
		m.update.Selected = int(msg.Runes[0] - '1')
		if m.update.Selected >= len(m.updateOptionLines()) {
			m.update.Selected = len(m.updateOptionLines()) - 1
		}
		m.refreshViewport()
		return m, nil
	case "enter":
		return m.completeUpdateSelection(m.update.Selected)
	default:
		return m, nil
	}
}

func (m *model) completeUpdateSelection(selection int) (tea.Model, tea.Cmd) {
	switch selection {
	case 0:
		m.exitAction = &ExternalAction{
			Kind:           ExternalActionUpdate,
			CurrentVersion: m.update.CurrentVersion,
			LatestVersion:  m.update.LatestVersion,
			Command:        append([]string{}, m.update.Command...),
		}
		m.update = updateState{}
		m.refreshViewport()
		return m, tea.Quit
	case 2:
		if err := config.SaveSkippedUpdateVersion(m.update.LatestVersion); err != nil {
			m.transcript.WriteError(fmt.Sprintf("update notice: %v", err))
		}
		if err := config.SaveUpdateSnoozeUntil(time.Time{}); err != nil {
			m.transcript.WriteError(fmt.Sprintf("update notice: %v", err))
		}
	default:
		if err := config.SaveUpdateSnoozeUntil(time.Now().Add(updateSnoozeDuration)); err != nil {
			m.transcript.WriteError(fmt.Sprintf("update notice: %v", err))
		}
	}
	m.update = updateState{}
	m.refreshViewport()
	if cmd := m.maybeStartOnboarding(); cmd != nil {
		return m, cmd
	}
	return m, nil
}
