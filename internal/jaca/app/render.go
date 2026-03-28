package app

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

type theme struct {
	background   lipgloss.Color
	border       lipgloss.Color
	borderStrong lipgloss.Color
	text         lipgloss.Color
	textSoft     lipgloss.Color
	textMuted    lipgloss.Color
	accent       lipgloss.Color
	accentSoft   lipgloss.Color
	success      lipgloss.Color
	successSoft  lipgloss.Color
	err          lipgloss.Color
}

var defaultTheme = theme{
	background:   lipgloss.Color("#0f1115"),
	border:       lipgloss.Color("#2a313c"),
	borderStrong: lipgloss.Color("#4a596d"),
	text:         lipgloss.Color("#f1ede4"),
	textSoft:     lipgloss.Color("#ddd7cb"),
	textMuted:    lipgloss.Color("#a7a39a"),
	accent:       lipgloss.Color("#d79a41"),
	accentSoft:   lipgloss.Color("#f1c27a"),
	success:      lipgloss.Color("#7bb07c"),
	successSoft:  lipgloss.Color("#a7d6a5"),
	err:          lipgloss.Color("#d46a5e"),
}

// The Go TUI keeps one global terminal background shade across the whole app.
// Structure comes from borders, spacing, and text hierarchy rather than
// per-zone background fills or panel/card chrome.

type viewModel struct {
	Phase         Phase
	Model         string
	WorkspaceRoot string
	Thinking      string
	SessionID     string
	MotionTick    int
	Transcript    string
	PromptValue   string
	PromptFooter  string
	VisibleZones  int
}

func renderView(vm viewModel) string {
	status := ""
	transcript := ""
	prompt := ""
	if vm.VisibleZones >= 1 {
		status = renderStatus(vm)
	}
	if vm.VisibleZones >= 2 {
		transcript = renderTranscript(vm)
	}
	if vm.VisibleZones >= 3 {
		prompt = renderPrompt(vm)
	}
	return lipgloss.JoinVertical(lipgloss.Left, status, transcript, prompt)
}

func renderStatus(vm viewModel) string {
	style := lipgloss.NewStyle().
		Foreground(defaultTheme.textMuted).
		BorderBottom(true).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(defaultTheme.border)
	switch vm.Phase {
	case PhaseStreaming:
		style = style.Foreground(defaultTheme.accentSoft).BorderForeground(defaultTheme.accent)
	case PhaseCompacting:
		style = style.Foreground(defaultTheme.accent).BorderForeground(defaultTheme.accentSoft)
	case PhaseCompleted:
		style = style.Foreground(defaultTheme.successSoft).BorderForeground(defaultTheme.success)
	case PhaseError:
		style = style.Foreground(defaultTheme.err).BorderForeground(defaultTheme.err)
	}
	return style.Render(buildStatusText(vm))
}

func renderTranscript(vm viewModel) string {
	style := lipgloss.NewStyle().
		Foreground(defaultTheme.text).
		BorderBottom(true).
		BorderForeground(defaultTheme.border)
	return style.Render(vm.Transcript)
}

func renderPrompt(vm viewModel) string {
	rowBorder := defaultTheme.border
	switch vm.Phase {
	case PhaseStreaming:
		rowBorder = defaultTheme.accent
	case PhaseCompacting:
		rowBorder = defaultTheme.accentSoft
	case PhaseCompleted:
		rowBorder = defaultTheme.success
	case PhaseError:
		rowBorder = defaultTheme.err
	}
	markerColor := defaultTheme.accent
	switch vm.Phase {
	case PhaseStreaming:
		markerColor = defaultTheme.accentSoft
	case PhaseCompleted:
		markerColor = defaultTheme.successSoft
	case PhaseError:
		markerColor = defaultTheme.err
	}
	footerColor := defaultTheme.textMuted
	switch vm.Phase {
	case PhaseStreaming:
		footerColor = defaultTheme.accentSoft
	case PhaseCompacting:
		footerColor = defaultTheme.accent
	case PhaseCompleted:
		footerColor = defaultTheme.successSoft
	case PhaseError:
		footerColor = defaultTheme.err
	}

	row := lipgloss.NewStyle().
		BorderTop(true).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(rowBorder).
		Render(
			lipgloss.JoinVertical(
				lipgloss.Left,
				lipgloss.JoinHorizontal(
					lipgloss.Left,
					lipgloss.NewStyle().Foreground(markerColor).Bold(true).Render(buildPromptMarkerText(vm.Phase, vm.MotionTick)),
					" ",
					lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(vm.PromptValue),
				),
				lipgloss.NewStyle().Foreground(footerColor).Render(buildPromptFooterText(vm.Phase, vm.PromptFooter)),
			),
		)
	return row
}

func buildPhaseLabel(phase Phase, motionTick int) string {
	if phase == PhaseStreaming || phase == PhaseCompacting {
		return fmt.Sprintf("%s%s", phase, strings.Repeat(".", (motionTick%3)+1))
	}
	if phase == PhaseCompleted {
		return "completed"
	}
	return string(phase)
}

func buildPromptMarkerText(phase Phase, motionTick int) string {
	switch phase {
	case PhaseStreaming:
		if motionTick%2 == 0 {
			return ">>"
		}
		return "> "
	case PhaseCompacting:
		if motionTick%2 == 0 {
			return "::"
		}
		return ".:"
	case PhaseCompleted:
		return "ok"
	case PhaseError:
		return "x "
	default:
		return "> "
	}
}

func buildPromptFooterText(phase Phase, override string) string {
	if override != "" {
		return override
	}
	switch phase {
	case PhaseStreaming:
		return "working  esc interrupt"
	case PhaseCompacting:
		return "compacting session"
	case PhaseCompleted:
		return "ready"
	case PhaseError:
		return "last run failed  edit prompt or retry"
	default:
		return "ready  /help  up/down recall  esc clear"
	}
}

func buildStatusText(vm viewModel) string {
	parts := []string{
		buildPhaseLabel(vm.Phase, vm.MotionTick),
		vm.Model,
		displayPath(vm.WorkspaceRoot),
	}
	if vm.Thinking != "" {
		parts = append(parts, fmt.Sprintf("thinking=%s", vm.Thinking))
	}
	if vm.SessionID != "" {
		session := vm.SessionID
		if len(session) > 8 {
			session = session[:8]
		}
		parts = append(parts, fmt.Sprintf("session=%s", session))
	}
	return strings.Join(parts, " | ")
}

func displayPath(path string) string {
	home, err := osUserHomeDir()
	if err != nil {
		return path
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	homeAbs, err := filepath.Abs(home)
	if err != nil {
		return abs
	}
	if abs == homeAbs {
		return "~"
	}
	prefix := homeAbs + string(filepath.Separator)
	if strings.HasPrefix(abs, prefix) {
		return "~" + abs[len(homeAbs):]
	}
	return abs
}

var osUserHomeDir = func() (string, error) {
	return os.UserHomeDir()
}
