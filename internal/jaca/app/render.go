package app

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/charmbracelet/lipgloss"
)

type theme struct {
	background  lipgloss.TerminalColor
	border      lipgloss.TerminalColor
	text        lipgloss.TerminalColor
	textSoft    lipgloss.TerminalColor
	textMuted   lipgloss.TerminalColor
	accent      lipgloss.TerminalColor
	accentSoft  lipgloss.TerminalColor
	success     lipgloss.TerminalColor
	successSoft lipgloss.TerminalColor
	err         lipgloss.TerminalColor
	errSoft     lipgloss.TerminalColor
}

type usageSnapshot struct {
	InputTokens   *int
	OutputTokens  *int
	TotalTokens   *int
	ContextWindow *float64
}

var defaultTheme = theme{
	background:  themeColor("#0f1115", "233", "0"),
	border:      themeColor("#2a313c", "238", "8"),
	text:        themeColor("#f1ede4", "255", "15"),
	textSoft:    themeColor("#ddd7cb", "252", "7"),
	textMuted:   themeColor("#a7a39a", "246", "8"),
	accent:      themeColor("#d79a41", "179", "11"),
	accentSoft:  themeColor("#f1c27a", "221", "11"),
	success:     themeColor("#7bb07c", "107", "10"),
	successSoft: themeColor("#a7d6a5", "151", "10"),
	err:         themeColor("#d46a5e", "167", "9"),
	errSoft:     themeColor("#bf5f5f", "131", "1"),
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
	Phase          Phase
	Width          int
	Height         int
	Model          string
	WorkspaceRoot  string
	Thinking       string
	SessionID      string
	MotionTick     int
	Transcript     string
	PromptValue    string
	PromptFooter   string
	RunElapsed     time.Duration
	Usage          usageSnapshot
	LinePulse      int
	SinceLastDelta time.Duration
	DetachedLive   bool
	VisibleZones   int
	SlashMenu      slashMenuState
	UpdatePrompt   updatePromptState
	Onboarding     onboardingOverlayView
	Auth           authOverlayView
}

type onboardingOverlayView struct {
	Active      bool
	Title       string
	Selected    int
	OptionLines []string
	HelpLines   []string
}

type authOverlayView struct {
	Active      bool
	Title       string
	Provider    string
	SecretLabel string
	InputValue  string
	HelpLines   []string
}

func renderView(vm viewModel) string {
	if vm.Onboarding.Active {
		return renderOnboardingOverlay(vm)
	}
	if vm.Auth.Active {
		return renderAuthOverlay(vm)
	}
	transcript := ""
	prompt := ""
	if vm.VisibleZones >= 1 {
		transcript = renderTranscript(vm)
	}
	if vm.VisibleZones >= 2 {
		prompt = renderPrompt(vm)
	}
	return lipgloss.JoinVertical(lipgloss.Left, transcript, prompt)
}

func renderOnboardingOverlay(vm viewModel) string {
	panelWidth := 60
	if vm.Width > 0 {
		panelWidth = min(68, max(48, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(defaultTheme.accentSoft).
		Bold(true).
		Render(vm.Onboarding.Title)
	rows := make([]string, 0, len(vm.Onboarding.OptionLines))
	for i, line := range vm.Onboarding.OptionLines {
		prefix := " "
		style := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
		if i == vm.Onboarding.Selected {
			prefix = ">"
			style = lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
		}
		rows = append(
			rows,
			lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(prefix)+" "+style.Render(line),
		)
	}
	helpLines := make([]string, 0, len(vm.Onboarding.HelpLines))
	for _, line := range vm.Onboarding.HelpLines {
		helpLines = append(
			helpLines,
			lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(line),
		)
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(defaultTheme.border).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			title,
			"",
			lipgloss.JoinVertical(lipgloss.Left, rows...),
			"",
			lipgloss.JoinVertical(lipgloss.Left, helpLines...),
		))

	width := vm.Width
	if width <= 0 {
		width = panelWidth + 8
	}
	height := vm.Height
	if height <= 0 {
		height = max(16, lipgloss.Height(panel)+6)
	}
	return lipgloss.Place(
		width,
		height,
		lipgloss.Center,
		lipgloss.Center,
		panel,
		lipgloss.WithWhitespaceChars(" "),
		lipgloss.WithWhitespaceForeground(defaultTheme.background),
	)
}

func renderAuthOverlay(vm viewModel) string {
	panelWidth := 56
	if vm.Width > 0 {
		panelWidth = min(64, max(44, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(defaultTheme.accentSoft).
		Bold(true).
		Render(vm.Auth.Title)
	subtitle := lipgloss.NewStyle().
		Foreground(defaultTheme.textSoft).
		Render(vm.Auth.SecretLabel)
	inputValue := vm.Auth.InputValue
	if strings.TrimSpace(inputValue) == "" {
		inputValue = " "
	}
	inputBox := lipgloss.NewStyle().
		Width(max(24, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(defaultTheme.accent).
		Foreground(defaultTheme.text).
		Render(inputValue)
	helpLines := make([]string, 0, len(vm.Auth.HelpLines))
	for index, line := range vm.Auth.HelpLines {
		style := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
		if index == 0 {
			style = lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
		}
		helpLines = append(helpLines, style.Render(line))
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(defaultTheme.border).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			title,
			subtitle,
			"",
			inputBox,
			"",
			lipgloss.JoinVertical(lipgloss.Left, helpLines...),
		))

	width := vm.Width
	if width <= 0 {
		width = panelWidth + 8
	}
	height := vm.Height
	if height <= 0 {
		height = max(16, lipgloss.Height(panel)+6)
	}
	return lipgloss.Place(
		width,
		height,
		lipgloss.Center,
		lipgloss.Center,
		panel,
		lipgloss.WithWhitespaceChars(" "),
		lipgloss.WithWhitespaceForeground(defaultTheme.background),
	)
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

	const promptPadX = 2

	width := vm.Width
	if width <= 0 {
		width = lipgloss.Width(vm.Transcript)
	}
	if width <= 0 {
		width = 80
	}
	ruleWidth := width - promptPadX*2
	if ruleWidth < 10 {
		ruleWidth = 10
	}
	topRail := renderTopRail(vm)
	topRule := renderPromptRule(ruleWidth, rowBorder)
	bottomRule := renderPromptRule(ruleWidth, rowBorder)

	promptParts := make([]string, 0, 8)
	promptParts = append(promptParts, "", "")
	if topRail != "" {
		promptParts = append(promptParts, topRail)
	}
	if vm.UpdatePrompt.Active {
		promptParts = append(promptParts, renderUpdatePrompt(vm.UpdatePrompt))
	}
	promptParts = append(promptParts, topRule)
	if vm.SlashMenu.Mode != slashMenuHidden && len(vm.SlashMenu.Rows) > 0 {
		promptParts = append(promptParts, renderSlashMenu(vm.SlashMenu))
	}
	promptParts = append(promptParts,
		lipgloss.JoinHorizontal(
			lipgloss.Left,
			lipgloss.NewStyle().Foreground(markerColor).Bold(true).Render(buildPromptMarkerText(vm.Phase, vm.MotionTick)),
			vm.PromptValue,
		),
		bottomRule,
		lipgloss.NewStyle().Foreground(footerColor).Render(
			buildPromptFooterText(vm),
		),
		"",
	)

	padStyle := lipgloss.NewStyle().PaddingLeft(promptPadX)
	for i, part := range promptParts {
		if part == "" {
			continue
		}
		promptParts[i] = padStyle.Render(part)
	}

	return lipgloss.JoinVertical(lipgloss.Left, promptParts...)
}

func promptHeight(vm viewModel) int {
	if vm.VisibleZones < 2 {
		return 0
	}
	return lipgloss.Height(renderPrompt(vm))
}

func renderTopRail(vm viewModel) string {
	indicator := buildTopRailIndicator(vm)
	if indicator == "" {
		return ""
	}
	if vm.Phase == PhaseStreaming {
		return lipgloss.JoinHorizontal(
			lipgloss.Left,
			renderWordWave(buildWorkingWave(vm.MotionTick), vm.MotionTick),
			" ",
			lipgloss.NewStyle().
				Foreground(defaultTheme.accentSoft).
				Render(formatElapsedClock(vm.RunElapsed)),
		)
	}
	if vm.Phase == PhaseCompacting {
		return lipgloss.JoinHorizontal(
			lipgloss.Left,
			renderWordWave(buildCompactingWave(vm.MotionTick), vm.MotionTick),
			" ",
			lipgloss.NewStyle().
				Foreground(defaultTheme.accentSoft).
				Render(formatElapsedClock(vm.RunElapsed)),
		)
	}
	return lipgloss.NewStyle().
		Foreground(defaultTheme.accentSoft).
		Render(indicator)
}

func renderUpdatePrompt(state updatePromptState) string {
	header := lipgloss.NewStyle().
		Foreground(defaultTheme.textSoft).
		Bold(true).
		Render(fmt.Sprintf("update available  %s -> %s", state.CurrentVersion, state.LatestVersion))
	command := lipgloss.NewStyle().
		Foreground(defaultTheme.textMuted).
		Render("runs: " + state.commandText())

	lines := []string{header, command}
	if state.Running {
		lines = append(lines, lipgloss.NewStyle().Foreground(defaultTheme.accentSoft).Render("updating..."))
	} else {
		for index, option := range state.options() {
			prefix := " "
			style := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
			if index == state.Selected {
				prefix = ">"
				style = lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
			}
			lines = append(lines, lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(prefix)+" "+style.Render(option))
		}
	}
	return lipgloss.JoinVertical(lipgloss.Left, lines...)
}

func renderPromptRule(width int, rowBorder lipgloss.TerminalColor) string {
	if width <= 0 {
		return ""
	}

	ruleColor := defaultTheme.text
	if rowBorder != defaultTheme.border {
		ruleColor = rowBorder
	}

	return lipgloss.NewStyle().Foreground(ruleColor).Render(strings.Repeat("─", width))
}

func buildTopRailIndicator(vm viewModel) string {
	if vm.Phase != PhaseStreaming && vm.Phase != PhaseCompacting {
		return ""
	}
	if vm.Phase == PhaseStreaming {
		return fmt.Sprintf("● %s %s", buildWorkingWave(vm.MotionTick), formatElapsedClock(vm.RunElapsed))
	}
	return fmt.Sprintf("● %s %s", buildCompactingWave(vm.MotionTick), formatElapsedClock(vm.RunElapsed))
}

func buildWorkingWave(motionTick int) string {
	return buildWordWave("Working", motionTick)
}

func buildCompactingWave(motionTick int) string {
	return buildWordWave("Compacting", motionTick)
}

func buildWordWave(word string, motionTick int) string {
	frames := []string{
		word,
		strings.ToLower(word),
	}
	runes := []rune(word)
	for i := 1; i < len(runes); i++ {
		frames = append(frames, highlightRune(runes, i), strings.ToLower(word))
	}
	for i := len(runes) - 2; i >= 1; i-- {
		frames = append(frames, highlightRune(runes, i), strings.ToLower(word))
	}
	return frames[motionTick%len(frames)]
}

func highlightRune(runes []rune, index int) string {
	highlighted := make([]rune, len(runes))
	for i, r := range runes {
		highlighted[i] = unicode.ToLower(r)
	}
	highlighted[index] = unicode.ToUpper(highlighted[index])
	return string(highlighted)
}

// breathingMarkerColor returns a smoothly pulsing color for the ● marker.
// Uses a cosine curve to blend between dim and bright over a 24-tick (~3.4s)
// cycle, inspired by Codex's shimmer_spans cosine-based intensity.
func breathingMarkerColor(motionTick int) lipgloss.TerminalColor {
	const period = 24
	t := 0.5 * (1.0 + math.Cos(2.0*math.Pi*float64(motionTick%period)/float64(period)))
	dimR, dimG, dimB := 0x3d, 0x35, 0x20
	hiR, hiG, hiB := 0xd7, 0x9a, 0x41
	r := uint8(float64(dimR) + t*float64(hiR-dimR))
	g := uint8(float64(dimG) + t*float64(hiG-dimG))
	b := uint8(float64(dimB) + t*float64(hiB-dimB))
	return lipgloss.Color(fmt.Sprintf("#%02x%02x%02x", r, g, b))
}

func renderWordWave(frame string, motionTick int) string {
	marker := lipgloss.NewStyle().Foreground(breathingMarkerColor(motionTick)).Render("●")

	base := lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
	active := lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Bold(true)
	parts := make([]string, 0, len(frame)+2)
	parts = append(parts, marker, " ")
	for _, r := range frame {
		s := string(r)
		if unicode.IsUpper(r) {
			parts = append(parts, active.Render(s))
			continue
		}
		parts = append(parts, base.Render(s))
	}
	return strings.Join(parts, "")
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

func buildPromptMarkerText(phase Phase, _ int) string {
	switch phase {
	case PhaseError:
		return "x "
	default:
		return "> "
	}
}

func buildPromptFooterText(vm viewModel) string {
	if vm.PromptFooter != "" {
		return vm.PromptFooter
	}
	switch vm.Phase {
	case PhaseStreaming:
		return joinFooterParts(
			"esc to interrupt",
			buildThinkingFooterText(vm.Thinking),
		)
	case PhaseCompacting:
		return "compacting session"
	case PhaseCompleted:
		usage := buildUsageFooterText(vm, true)
		if usage == "" {
			return "completed"
		}
		return joinFooterParts("completed", usage)
	case PhaseError:
		return "last run failed  edit prompt or retry"
	default:
		return buildIdleFooterText(vm)
	}
}

func buildIdleFooterText(vm viewModel) string {
	parts := []string{
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
	if usage := buildUsageFooterText(vm, false); usage != "" {
		parts = append(parts, usage)
	}
	return strings.Join(parts, "  ")
}

func buildUsageFooterText(vm viewModel, detailed bool) string {
	parts := []string{}
	if detailed {
		if vm.Usage.InputTokens != nil {
			parts = append(parts, fmt.Sprintf("%d in", *vm.Usage.InputTokens))
		}
		if vm.Usage.OutputTokens != nil {
			parts = append(parts, fmt.Sprintf("%d out", *vm.Usage.OutputTokens))
		}
	}
	if vm.Usage.TotalTokens != nil {
		parts = append(parts, fmt.Sprintf("%d tok", *vm.Usage.TotalTokens))
	} else if !detailed {
		if vm.Usage.InputTokens != nil {
			parts = append(parts, fmt.Sprintf("%d in", *vm.Usage.InputTokens))
		}
		if vm.Usage.OutputTokens != nil {
			parts = append(parts, fmt.Sprintf("%d out", *vm.Usage.OutputTokens))
		}
	}
	if vm.Usage.ContextWindow != nil {
		parts = append(parts, fmt.Sprintf("%d%% ctx", int(math.Round(*vm.Usage.ContextWindow*100))))
	}
	return joinFooterParts(parts...)
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

func formatElapsedClock(d time.Duration) string {
	if d <= 0 {
		return "00:00"
	}
	totalSeconds := int(d.Seconds())
	if totalSeconds < 0 {
		totalSeconds = 0
	}
	minutes := totalSeconds / 60
	seconds := totalSeconds % 60
	if minutes > 99 {
		minutes = 99
	}
	return fmt.Sprintf("%02d:%02d", minutes, seconds)
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
		return filepath.ToSlash(path)
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return filepath.ToSlash(path)
	}
	homeAbs, err := filepath.Abs(home)
	if err != nil {
		return filepath.ToSlash(abs)
	}
	if abs == homeAbs {
		return "~"
	}
	prefix := homeAbs + string(filepath.Separator)
	if strings.HasPrefix(abs, prefix) {
		return filepath.ToSlash("~" + abs[len(homeAbs):])
	}
	return filepath.ToSlash(abs)
}

var osUserHomeDir = func() (string, error) {
	return os.UserHomeDir()
}
