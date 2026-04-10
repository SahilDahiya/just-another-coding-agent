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
	Phase               Phase
	Width               int
	Height              int
	Model               string
	WorkspaceRoot       string
	Thinking            string
	SessionID           string
	SessionName         string
	MotionTick          int
	Transcript          string
	PromptValue         string
	PromptFooter        string
	RunElapsed          time.Duration
	AwaitingFirstOutput bool
	Usage               usageSnapshot
	QueuedNext          []string
	QueuedLater         []string
	LinePulse           int
	SinceLastDelta      time.Duration
	DetachedLive        bool
	VisibleZones        int
	SlashMenu           slashMenuState
	Onboarding          onboardingOverlayView
	Auth                authOverlayView
	Login               loginOverlayView
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

type loginOverlayView struct {
	Active       bool
	Provider     string
	AuthURL      string
	Instructions string
	InputValue   string
}

func renderView(vm viewModel) string {
	if vm.Onboarding.Active {
		return renderOnboardingOverlay(vm)
	}
	if vm.Login.Active {
		return renderLoginOverlay(vm)
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

func renderLoginOverlay(vm viewModel) string {
	panelWidth := 68
	if vm.Width > 0 {
		panelWidth = min(76, max(52, vm.Width-8))
	}
	titleText := "ChatGPT Login"
	subtitleText := "Browser callback auto-completes. Paste only if it does not return."
	title := lipgloss.NewStyle().
		Foreground(defaultTheme.accentSoft).
		Bold(true).
		Render(titleText)
	subtitle := lipgloss.NewStyle().
		Foreground(defaultTheme.textSoft).
		Render(subtitleText)
	url := vm.Login.AuthURL
	if strings.TrimSpace(url) == "" {
		url = "starting login..."
	}
	urlBox := lipgloss.NewStyle().
		Width(max(32, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(defaultTheme.border).
		Foreground(defaultTheme.textSoft).
		Render(url)
	inputValue := vm.Login.InputValue
	if strings.TrimSpace(inputValue) == "" {
		inputValue = " "
	}
	inputBox := lipgloss.NewStyle().
		Width(max(32, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(defaultTheme.accent).
		Foreground(defaultTheme.text).
		Render(inputValue)
	lines := []string{
		lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(vm.Login.Instructions),
		lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("Enter completes manually. Esc cancels."),
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
			urlBox,
			"",
			inputBox,
			"",
			lipgloss.JoinVertical(lipgloss.Left, lines...),
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
		style = style.Foreground(defaultTheme.errSoft).BorderForeground(defaultTheme.errSoft)
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
		rowBorder = defaultTheme.errSoft
	}
	markerColor := defaultTheme.accent
	switch vm.Phase {
	case PhaseStreaming:
		markerColor = defaultTheme.accentSoft
	case PhaseCompleted:
		markerColor = defaultTheme.successSoft
	case PhaseError:
		markerColor = defaultTheme.errSoft
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
		footerColor = defaultTheme.errSoft
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
	if vm.Login.Provider != "" && vm.Phase != PhaseStreaming && vm.Phase != PhaseCompacting {
		promptParts = append(promptParts, "")
	} else {
		promptParts = append(promptParts, "", "")
	}
	if topRail != "" {
		promptParts = append(promptParts, topRail)
	}
	if queuePreview := renderQueuedInputPreview(vm, ruleWidth); queuePreview != "" {
		promptParts = append(promptParts, queuePreview)
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
		renderPromptFooterLine(
			ruleWidth,
			buildPromptFooterText(vm),
			buildPromptCopyHint(),
			footerColor,
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

func renderPromptFooterLine(
	width int,
	left string,
	right string,
	leftColor lipgloss.TerminalColor,
) string {
	leftStyle := lipgloss.NewStyle().Foreground(leftColor)
	if strings.TrimSpace(right) == "" {
		return leftStyle.Render(left)
	}
	if width <= 0 {
		return leftStyle.Render(left)
	}
	leftWidth := lipgloss.Width(left)
	rightWidth := lipgloss.Width(right)
	if leftWidth+rightWidth+2 > width {
		return leftStyle.Render(left)
	}
	gap := strings.Repeat(" ", width-leftWidth-rightWidth)
	return leftStyle.Render(left) +
		gap +
		lipgloss.NewStyle().
			Foreground(defaultTheme.textMuted).
			Render(right)
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
		wave := buildWorkingWave(vm.MotionTick)
		if vm.AwaitingFirstOutput {
			wave = buildThinkingWave(vm.MotionTick)
		}
		return renderActiveTopRail(wave, vm.MotionTick, vm.RunElapsed)
	}
	if vm.Phase == PhaseCompacting {
		return renderActiveTopRail(buildCompactingWave(vm.MotionTick), vm.MotionTick, vm.RunElapsed)
	}
	return lipgloss.NewStyle().
		Foreground(defaultTheme.accentSoft).
		Render(indicator)
}

func renderActiveTopRail(wave string, motionTick int, elapsed time.Duration) string {
	return lipgloss.JoinHorizontal(
		lipgloss.Left,
		renderWordWave(wave, motionTick),
		lipgloss.NewStyle().
			Foreground(defaultTheme.accentSoft).
			Render("…"),
		lipgloss.NewStyle().
			Foreground(defaultTheme.textMuted).
			Render(" ("),
		lipgloss.NewStyle().
			Foreground(defaultTheme.accentSoft).
			Render(formatTopRailElapsed(elapsed)),
		lipgloss.NewStyle().
			Foreground(defaultTheme.textMuted).
			Render(" · esc to interrupt)"),
	)
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
	if vm.Phase != PhaseStreaming && vm.Phase != PhaseCompacting && vm.Login.Provider != "" {
		return fmt.Sprintf("● %s (esc to cancel)", buildLoginWave(vm.MotionTick))
	}
	if vm.Phase != PhaseStreaming && vm.Phase != PhaseCompacting {
		return ""
	}
	if vm.Phase == PhaseStreaming {
		wave := buildWorkingWave(vm.MotionTick)
		if vm.AwaitingFirstOutput {
			wave = buildThinkingWave(vm.MotionTick)
		}
		return fmt.Sprintf("● %s… (%s · esc to interrupt)", wave, formatTopRailElapsed(vm.RunElapsed))
	}
	return fmt.Sprintf("● %s… (%s · esc to interrupt)", buildCompactingWave(vm.MotionTick), formatTopRailElapsed(vm.RunElapsed))
}

func buildThinkingWave(motionTick int) string {
	return buildWordWave("Thinking", motionTick)
}

func buildWorkingWave(motionTick int) string {
	return buildWordWave("Working", motionTick)
}

func buildCompactingWave(motionTick int) string {
	return buildWordWave("Compacting", motionTick)
}

func buildLoginWave(motionTick int) string {
	return buildWordWave("Logging in", motionTick)
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
	case PhaseStreaming, PhaseCompacting:
		parts := []string{vm.Model}
		if vm.Thinking != "" {
			parts = append(parts, fmt.Sprintf("thinking=%s", vm.Thinking))
		}
		return joinFooterParts(parts...)
	case PhaseCompleted:
		usage := buildUsageFooterText(vm, true)
		if usage == "" {
			return "completed"
		}
		return joinFooterParts("completed", usage)
	case PhaseError:
		return "ready ● edit prompt or try something else"
	default:
		return buildIdleFooterText(vm)
	}
}

func buildPromptCopyHint() string {
	return "Shift+drag to copy"
}

func renderQueuedInputPreview(vm viewModel, width int) string {
	if len(vm.QueuedNext) == 0 && len(vm.QueuedLater) == 0 {
		return ""
	}
	previewStyle := lipgloss.NewStyle().
		BorderLeft(true).
		BorderForeground(defaultTheme.border).
		PaddingLeft(1)
	sectionGap := ""
	var sections []string
	if len(vm.QueuedNext) > 0 {
		sections = append(sections, renderQueuedInputSection(
			"After current tool phase",
			"Esc sends now",
			vm.QueuedNext,
			width,
		))
		sectionGap = "\n"
	}
	if len(vm.QueuedLater) > 0 {
		sections = append(sections, renderQueuedInputSection(
			"At end of turn",
			"",
			vm.QueuedLater,
			width,
		))
	}
	return previewStyle.Render(strings.Join(sections, sectionGap))
}

func renderQueuedInputSection(title string, hint string, prompts []string, width int) string {
	header := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(title)
	count := lipgloss.NewStyle().Foreground(defaultTheme.accentSoft).Render(
		fmt.Sprintf("%d queued", len(prompts)),
	)
	headerLine := lipgloss.JoinHorizontal(lipgloss.Left, header, "  ", count)
	if hint != "" {
		headerLine = lipgloss.JoinHorizontal(
			lipgloss.Left,
			headerLine,
			"  ",
			lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(hint),
		)
	}
	lines := []string{headerLine}
	maxPrompts := minInt(len(prompts), 3)
	for i := 0; i < maxPrompts; i++ {
		lines = append(lines, renderQueuedPromptLine(prompts[i], width))
	}
	if len(prompts) > maxPrompts {
		lines = append(lines, lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("  …"))
	}
	return strings.Join(lines, "\n")
}

func renderQueuedPromptLine(prompt string, width int) string {
	trimmed := strings.TrimSpace(prompt)
	firstLine := trimmed
	if idx := strings.IndexByte(firstLine, '\n'); idx >= 0 {
		firstLine = firstLine[:idx]
	}
	firstLine = strings.TrimSpace(firstLine)
	if firstLine == "" {
		firstLine = "(blank)"
	}
	maxWidth := width - 12
	if maxWidth < 16 {
		maxWidth = 16
	}
	runes := []rune(firstLine)
	if len(runes) > maxWidth {
		firstLine = string(runes[:maxWidth-1]) + "…"
	}
	return lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render("  ↳ " + firstLine)
}

func minInt(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func buildIdleFooterText(vm viewModel) string {
	parts := []string{
		vm.Model,
		displayPath(vm.WorkspaceRoot),
	}
	if vm.Thinking != "" {
		parts = append(parts, fmt.Sprintf("thinking=%s", vm.Thinking))
	}
	if usage := buildUsageFooterText(vm, false); usage != "" {
		parts = append(parts, usage)
	}
	if vm.SessionName != "" {
		parts = append(parts, vm.SessionName)
	}
	return joinFooterParts(parts...)
}

func buildUsageFooterText(vm viewModel, detailed bool) string {
	parts := []string{}
	if vm.Usage.ContextWindow != nil {
		contextLeft := 1 - *vm.Usage.ContextWindow
		contextLeft = math.Max(0, math.Min(1, contextLeft))
		parts = append(parts, fmt.Sprintf("%d%% left", int(math.Round(contextLeft*100))))
	}
	return joinFooterParts(parts...)
}

func formatTopRailElapsed(d time.Duration) string {
	if d <= 0 {
		return "0s"
	}
	totalSeconds := int(d.Seconds())
	if totalSeconds <= 0 {
		return "0s"
	}
	if totalSeconds < 60 {
		return fmt.Sprintf("%ds", totalSeconds)
	}
	minutes := totalSeconds / 60
	seconds := totalSeconds % 60
	if seconds == 0 {
		return fmt.Sprintf("%dm", minutes)
	}
	return fmt.Sprintf("%dm %ds", minutes, seconds)
}

func joinFooterParts(parts ...string) string {
	filtered := make([]string, 0, len(parts))
	for _, part := range parts {
		if strings.TrimSpace(part) == "" {
			continue
		}
		filtered = append(filtered, part)
	}
	return strings.Join(filtered, " ● ")
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
