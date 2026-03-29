package app

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

var brailleSpinner = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}


type theme struct {
	background   lipgloss.TerminalColor
	border       lipgloss.TerminalColor
	borderStrong lipgloss.TerminalColor
	text         lipgloss.TerminalColor
	textSoft     lipgloss.TerminalColor
	textMuted    lipgloss.TerminalColor
	accent       lipgloss.TerminalColor
	accentSoft   lipgloss.TerminalColor
	success      lipgloss.TerminalColor
	successSoft  lipgloss.TerminalColor
	err          lipgloss.TerminalColor
	errSoft      lipgloss.TerminalColor
}

var defaultTheme = theme{
	background:   themeColor("#0f1115", "233", "0"),
	border:       themeColor("#2a313c", "238", "8"),
	borderStrong: themeColor("#4a596d", "244", "7"),
	text:         themeColor("#f1ede4", "255", "15"),
	textSoft:     themeColor("#ddd7cb", "252", "7"),
	textMuted:    themeColor("#a7a39a", "246", "8"),
	accent:       themeColor("#d79a41", "179", "11"),
	accentSoft:   themeColor("#f1c27a", "221", "11"),
	success:      themeColor("#7bb07c", "107", "10"),
	successSoft:  themeColor("#a7d6a5", "151", "10"),
	err:          themeColor("#d46a5e", "167", "9"),
	errSoft:      themeColor("#bf5f5f", "131", "1"),
}

func themeColor(trueColor string, ansi256 string, ansi string) lipgloss.TerminalColor {
	return lipgloss.CompleteColor{
		TrueColor: trueColor,
		ANSI256:   ansi256,
		ANSI:      ansi,
	}
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
	RunElapsed    time.Duration
	VisibleZones  int
	SlashMenu     slashMenuState
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

	promptParts := make([]string, 0, 3)
	if vm.SlashMenu.Mode != slashMenuHidden && len(vm.SlashMenu.Rows) > 0 {
		promptParts = append(promptParts, renderSlashMenu(vm.SlashMenu))
	}
	promptParts = append(promptParts,
		lipgloss.JoinHorizontal(
			lipgloss.Left,
			lipgloss.NewStyle().Foreground(markerColor).Bold(true).Render(buildPromptMarkerText(vm.Phase, vm.MotionTick)),
			" ",
			lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(vm.PromptValue),
		),
		lipgloss.NewStyle().Foreground(footerColor).Render(buildPromptFooterText(vm.Phase, vm.Thinking, vm.PromptFooter, vm.RunElapsed)),
	)

	row := lipgloss.NewStyle().
		BorderTop(true).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(rowBorder).
		Render(
			lipgloss.JoinVertical(lipgloss.Left, promptParts...),
		)
	return row
}

func renderSlashMenu(state slashMenuState) string {
	rows := visibleSlashMenuRows(state)
	lines := make([]string, 0, len(rows))
	selectedStart := max(0, state.Selected-(maxSlashMenuRows/2))
	if selectedStart+len(rows) > len(state.Rows) {
		selectedStart = len(state.Rows) - len(rows)
	}
	for idx, row := range rows {
		actualIndex := selectedStart + idx
		prefix := " "
		valueColor := defaultTheme.textMuted
		descColor := defaultTheme.textMuted
		if actualIndex == state.Selected {
			prefix = ">"
			valueColor = defaultTheme.accentSoft
			descColor = defaultTheme.textSoft
		}
		lines = append(lines,
			lipgloss.JoinHorizontal(
				lipgloss.Left,
				lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(prefix),
				" ",
				lipgloss.NewStyle().Foreground(valueColor).Render(padRight(row.Value, 16)),
				lipgloss.NewStyle().Foreground(descColor).Render(row.Description),
			),
		)
	}
	return lipgloss.JoinVertical(lipgloss.Left, lines...)
}

func visibleSlashMenuRows(state slashMenuState) []slashSuggestion {
	if len(state.Rows) <= maxSlashMenuRows {
		return state.Rows
	}
	start := state.Selected - (maxSlashMenuRows / 2)
	if start < 0 {
		start = 0
	}
	end := start + maxSlashMenuRows
	if end > len(state.Rows) {
		end = len(state.Rows)
		start = end - maxSlashMenuRows
	}
	return state.Rows[start:end]
}

func padRight(value string, width int) string {
	if len(value) >= width {
		return value + "  "
	}
	return value + strings.Repeat(" ", width-len(value)+2)
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
		return brailleSpinner[motionTick%len(brailleSpinner)] + " "
	case PhaseCompacting:
		return brailleSpinner[motionTick%len(brailleSpinner)] + " "
	case PhaseCompleted:
		return "※ "
	case PhaseError:
		return "x "
	default:
		return "> "
	}
}

func buildPromptFooterText(phase Phase, thinking string, override string, elapsed time.Duration) string {
	if override != "" {
		return override
	}
	switch phase {
	case PhaseStreaming:
		return joinFooterParts(
			formatElapsed(elapsed),
			"esc to interrupt",
			buildThinkingFooterText(thinking),
		)
	case PhaseCompacting:
		return "compacting session"
	case PhaseCompleted:
		return "ready"
	case PhaseError:
		return "last run failed  edit prompt or retry"
	default:
		return "ready"
	}
}

func formatElapsed(d time.Duration) string {
	if d <= 0 {
		return ""
	}
	secs := int(d.Seconds())
	if secs < 60 {
		return fmt.Sprintf("%ds", secs)
	}
	mins := secs / 60
	remaining := secs % 60
	return fmt.Sprintf("%dm %ds", mins, remaining)
}

func buildThinkingFooterText(thinking string) string {
	switch thinking {
	case "":
		return ""
	case "true":
		return "◐ on · effort"
	case "false":
		return "◐ off · effort"
	default:
		return fmt.Sprintf("◐ %s · effort", thinking)
	}
}

func joinFooterParts(parts ...string) string {
	filtered := make([]string, 0, len(parts))
	for _, part := range parts {
		if strings.TrimSpace(part) == "" {
			continue
		}
		filtered = append(filtered, part)
	}
	return strings.Join(filtered, "  ")
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
