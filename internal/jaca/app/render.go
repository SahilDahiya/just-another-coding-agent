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
	userFill    lipgloss.TerminalColor
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
	userFill:    themeColor("#171b22", "235", "8"),
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

var fullAccessTheme = theme{
	background:  themeColor("#0f1115", "233", "0"),
	border:      themeColor("#4a2f33", "238", "8"),
	userFill:    themeColor("#1b171b", "235", "8"),
	text:        themeColor("#f1ede4", "255", "15"),
	textSoft:    themeColor("#ddd7cb", "252", "7"),
	textMuted:   themeColor("#b0a4a5", "246", "8"),
	accent:      themeColor("#d46a5e", "167", "9"),
	accentSoft:  themeColor("#e6a29a", "210", "9"),
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
// Structure comes from borders, spacing, and text hierarchy, with one small
// exception: user turns get a subtle shaded band so they read as distinct
// transcript blocks without turning the whole interface into card chrome.

type viewModel struct {
	Phase               Phase
	Width               int
	Height              int
	Model               string
	PermissionPreset    string
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
	Trust               trustOverlayView
	Update              updateOverlayView
	Onboarding          onboardingOverlayView
	Auth                authOverlayView
	Login               loginOverlayView
	Approval            approvalPromptView
}

func themeForPermissionPreset(preset string) theme {
	switch preset {
	case "full_access":
		return fullAccessTheme
	default:
		return defaultTheme
	}
}

type updateOverlayView struct {
	Active         bool
	Title          string
	CurrentVersion string
	LatestVersion  string
	Selected       int
	OptionLines    []string
	HelpLines      []string
}

type trustOverlayView struct {
	Active      bool
	Title       string
	BodyLines   []string
	Selected    int
	OptionLines []string
	HelpLines   []string
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

type approvalPromptView struct {
	Active      bool
	Title       string
	Reason      string
	Details     []string
	Selected    int
	OptionLines []string
	HelpLines   []string
}

func renderView(vm viewModel) string {
	if vm.Trust.Active {
		return renderTrustOverlay(vm)
	}
	if vm.Update.Active {
		return renderUpdateOverlay(vm)
	}
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

func renderTrustOverlay(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	panelWidth := 68
	if vm.Width > 0 {
		panelWidth = min(76, max(52, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(vm.Trust.Title)
	bodyLines := make([]string, 0, len(vm.Trust.BodyLines))
	for _, line := range vm.Trust.BodyLines {
		bodyLines = append(
			bodyLines,
			lipgloss.NewStyle().Foreground(th.textSoft).Render(line),
		)
	}
	rows := make([]string, 0, len(vm.Trust.OptionLines))
	for i, line := range vm.Trust.OptionLines {
		prefix := " "
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if i == vm.Trust.Selected {
			prefix = ">"
			style = lipgloss.NewStyle().Foreground(th.accentSoft)
		}
		rows = append(
			rows,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(prefix)+" "+style.Render(line),
		)
	}
	helpLines := make([]string, 0, len(vm.Trust.HelpLines))
	for _, line := range vm.Trust.HelpLines {
		helpLines = append(
			helpLines,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(line),
		)
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(th.border).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			title,
			"",
			lipgloss.JoinVertical(lipgloss.Left, bodyLines...),
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
		lipgloss.WithWhitespaceForeground(th.background),
	)
}

func renderUpdateOverlay(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	panelWidth := 60
	if vm.Width > 0 {
		panelWidth = min(68, max(48, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(vm.Update.Title)
	subtitle := lipgloss.NewStyle().
		Foreground(th.textSoft).
		Render(fmt.Sprintf("JACA %s -> %s", vm.Update.CurrentVersion, vm.Update.LatestVersion))
	rows := make([]string, 0, len(vm.Update.OptionLines))
	for i, line := range vm.Update.OptionLines {
		prefix := " "
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if i == vm.Update.Selected {
			prefix = ">"
			style = lipgloss.NewStyle().Foreground(th.accentSoft)
		}
		rows = append(
			rows,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(prefix)+" "+style.Render(line),
		)
	}
	helpLines := make([]string, 0, len(vm.Update.HelpLines))
	for index, line := range vm.Update.HelpLines {
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if index == 0 {
			style = lipgloss.NewStyle().Foreground(th.textSoft)
		}
		helpLines = append(helpLines, style.Render(line))
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(th.border).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			title,
			subtitle,
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
		lipgloss.WithWhitespaceForeground(th.background),
	)
}

func renderOnboardingOverlay(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	panelWidth := 60
	if vm.Width > 0 {
		panelWidth = min(68, max(48, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(vm.Onboarding.Title)
	rows := make([]string, 0, len(vm.Onboarding.OptionLines))
	for i, line := range vm.Onboarding.OptionLines {
		prefix := " "
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if i == vm.Onboarding.Selected {
			prefix = ">"
			style = lipgloss.NewStyle().Foreground(th.accentSoft)
		}
		rows = append(
			rows,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(prefix)+" "+style.Render(line),
		)
	}
	helpLines := make([]string, 0, len(vm.Onboarding.HelpLines))
	for _, line := range vm.Onboarding.HelpLines {
		helpLines = append(
			helpLines,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(line),
		)
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(th.border).
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
		lipgloss.WithWhitespaceForeground(th.background),
	)
}

func renderAuthOverlay(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	panelWidth := 56
	if vm.Width > 0 {
		panelWidth = min(64, max(44, vm.Width-8))
	}
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(vm.Auth.Title)
	subtitle := lipgloss.NewStyle().
		Foreground(th.textSoft).
		Render(vm.Auth.SecretLabel)
	inputValue := vm.Auth.InputValue
	if strings.TrimSpace(inputValue) == "" {
		inputValue = " "
	}
	inputBox := lipgloss.NewStyle().
		Width(max(24, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(th.accent).
		Foreground(th.text).
		Render(inputValue)
	helpLines := make([]string, 0, len(vm.Auth.HelpLines))
	for index, line := range vm.Auth.HelpLines {
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if index == 0 {
			style = lipgloss.NewStyle().Foreground(th.textSoft)
		}
		helpLines = append(helpLines, style.Render(line))
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(th.border).
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
		lipgloss.WithWhitespaceForeground(th.background),
	)
}

func renderLoginOverlay(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	panelWidth := 68
	if vm.Width > 0 {
		panelWidth = min(76, max(52, vm.Width-8))
	}
	titleText := "ChatGPT Login"
	subtitleText := "Finish login in the browser. Paste the browser code here only if needed."
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(titleText)
	subtitle := lipgloss.NewStyle().
		Foreground(th.textSoft).
		Render(subtitleText)
	url := vm.Login.AuthURL
	if strings.TrimSpace(url) == "" {
		url = "starting login..."
	}
	urlBox := lipgloss.NewStyle().
		Width(max(32, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(th.border).
		Foreground(th.textSoft).
		Render(url)
	inputValue := vm.Login.InputValue
	if strings.TrimSpace(inputValue) == "" {
		inputValue = " "
	}
	inputBox := lipgloss.NewStyle().
		Width(max(32, panelWidth-8)).
		Padding(0, 1).
		Border(lipgloss.NormalBorder()).
		BorderForeground(th.accent).
		Foreground(th.text).
		Render(inputValue)
	lines := []string{
		lipgloss.NewStyle().Foreground(th.textMuted).Render(vm.Login.Instructions),
		lipgloss.NewStyle().Foreground(th.textMuted).Render("Enter completes manually. Esc cancels."),
	}
	panel := lipgloss.NewStyle().
		Width(panelWidth).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(th.border).
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
		lipgloss.WithWhitespaceForeground(th.background),
	)
}

func renderApprovalPrompt(approval approvalPromptView, th theme) string {
	title := lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Bold(true).
		Render(approval.Title)
	reason := lipgloss.NewStyle().
		Foreground(th.textSoft).
		Render(approval.Reason)
	detailLines := make([]string, 0, len(approval.Details))
	for _, line := range approval.Details {
		detailLines = append(
			detailLines,
			lipgloss.NewStyle().
				Foreground(th.textMuted).
				Render("  "+line),
		)
	}
	rows := make([]string, 0, len(approval.OptionLines))
	for i, line := range approval.OptionLines {
		prefix := " "
		style := lipgloss.NewStyle()
		switch i {
		case 0:
			style = style.Foreground(th.success)
		default:
			style = style.Foreground(th.err)
		}
		if i == approval.Selected {
			prefix = ">"
			switch i {
			case 0:
				style = lipgloss.NewStyle().
					Foreground(th.successSoft).
					Bold(true)
			default:
				style = lipgloss.NewStyle().
					Foreground(th.errSoft).
					Bold(true)
			}
		}
		rows = append(
			rows,
			lipgloss.NewStyle().Foreground(th.textMuted).Render(prefix)+" "+style.Render(line),
		)
	}
	helpLines := make([]string, 0, len(approval.HelpLines))
	for index, line := range approval.HelpLines {
		style := lipgloss.NewStyle().Foreground(th.textMuted)
		if index == 0 {
			style = lipgloss.NewStyle().Foreground(th.textSoft)
		}
		helpLines = append(
			helpLines,
			style.Render(line),
		)
	}
	return lipgloss.JoinVertical(
		lipgloss.Left,
		title,
		"",
		reason,
		lipgloss.JoinVertical(lipgloss.Left, detailLines...),
		"",
		lipgloss.JoinVertical(lipgloss.Left, rows...),
		"",
		lipgloss.JoinVertical(lipgloss.Left, helpLines...),
	)
}

func renderStatus(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	style := lipgloss.NewStyle().
		Foreground(th.textMuted).
		BorderBottom(true).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(th.border)
	switch vm.Phase {
	case PhaseStreaming:
		style = style.Foreground(th.accentSoft).BorderForeground(th.accent)
	case PhaseCompacting:
		style = style.Foreground(th.accent).BorderForeground(th.accentSoft)
	case PhaseCompleted:
		style = style.Foreground(th.successSoft).BorderForeground(th.success)
	case PhaseError:
		style = style.Foreground(th.errSoft).BorderForeground(th.errSoft)
	}
	return style.Render(buildStatusText(vm))
}

func renderTranscript(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	style := lipgloss.NewStyle().
		Foreground(th.text).
		BorderBottom(true).
		BorderForeground(th.border)
	return style.Render(vm.Transcript)
}

func renderPrompt(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	rowBorder := th.border
	switch vm.Phase {
	case PhaseStreaming:
		rowBorder = th.accent
	case PhaseCompacting:
		rowBorder = th.accentSoft
	case PhaseCompleted:
		rowBorder = th.success
	case PhaseError:
		rowBorder = th.errSoft
	}
	markerColor := th.accent
	switch vm.Phase {
	case PhaseStreaming:
		markerColor = th.accentSoft
	case PhaseCompleted:
		markerColor = th.successSoft
	case PhaseError:
		markerColor = th.errSoft
	}
	footerColor := th.textMuted
	switch vm.Phase {
	case PhaseStreaming:
		footerColor = th.accentSoft
	case PhaseCompacting:
		footerColor = th.accent
	case PhaseCompleted:
		footerColor = th.successSoft
	case PhaseError:
		footerColor = th.errSoft
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
		promptParts = append(promptParts, renderSlashMenuForTheme(vm.SlashMenu, th))
	}
	if vm.Approval.Active {
		promptParts = append(promptParts, renderApprovalPrompt(vm.Approval, th))
	} else {
		promptParts = append(promptParts,
			lipgloss.JoinHorizontal(
				lipgloss.Left,
				lipgloss.NewStyle().Foreground(markerColor).Bold(true).Render(buildPromptMarkerText(vm.Phase, vm.MotionTick)),
				vm.PromptValue,
			),
		)
	}
	promptParts = append(promptParts,
		bottomRule,
		renderPromptFooterLine(
			ruleWidth,
			buildPromptFooterText(vm),
			buildPromptCopyHint(),
			footerColor,
			th,
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
	th theme,
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
			Foreground(th.textMuted).
			Render(right)
}

func promptHeight(vm viewModel) int {
	if vm.VisibleZones < 2 {
		return 0
	}
	return lipgloss.Height(renderPrompt(vm))
}

func renderTopRail(vm viewModel) string {
	th := themeForPermissionPreset(vm.PermissionPreset)
	indicator := buildTopRailIndicator(vm)
	if indicator == "" {
		return ""
	}
	if vm.Phase == PhaseStreaming {
		wave := buildWorkingWave(vm.MotionTick)
		if vm.AwaitingFirstOutput {
			wave = buildThinkingWave(vm.MotionTick)
		}
		return renderActiveTopRail(wave, vm.MotionTick, vm.RunElapsed, th)
	}
	if vm.Phase == PhaseCompacting {
		return renderActiveTopRail(buildCompactingWave(vm.MotionTick), vm.MotionTick, vm.RunElapsed, th)
	}
	return lipgloss.NewStyle().
		Foreground(th.accentSoft).
		Render(indicator)
}

func renderActiveTopRail(wave string, motionTick int, elapsed time.Duration, th theme) string {
	return lipgloss.JoinHorizontal(
		lipgloss.Left,
		renderWordWaveForTheme(wave, motionTick, th),
		lipgloss.NewStyle().
			Foreground(th.accentSoft).
			Render("…"),
		lipgloss.NewStyle().
			Foreground(th.textMuted).
			Render(" ("),
		lipgloss.NewStyle().
			Foreground(th.accentSoft).
			Render(formatTopRailElapsed(elapsed)),
		lipgloss.NewStyle().
			Foreground(th.textMuted).
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
	return breathingMarkerColorForTheme(motionTick, defaultTheme)
}

func breathingMarkerColorForTheme(motionTick int, th theme) lipgloss.TerminalColor {
	const period = 24
	t := 0.5 * (1.0 + math.Cos(2.0*math.Pi*float64(motionTick%period)/float64(period)))
	dimR, dimG, dimB := terminalColorRGB(th.border)
	hiR, hiG, hiB := terminalColorRGB(th.accent)
	r := uint8(float64(dimR) + t*float64(hiR-dimR))
	g := uint8(float64(dimG) + t*float64(hiG-dimG))
	b := uint8(float64(dimB) + t*float64(hiB-dimB))
	return lipgloss.Color(fmt.Sprintf("#%02x%02x%02x", r, g, b))
}

func renderWordWave(frame string, motionTick int) string {
	return renderWordWaveForTheme(frame, motionTick, defaultTheme)
}

func renderWordWaveForTheme(frame string, motionTick int, th theme) string {
	marker := lipgloss.NewStyle().Foreground(breathingMarkerColorForTheme(motionTick, th)).Render("●")

	base := lipgloss.NewStyle().Foreground(th.accentSoft)
	active := lipgloss.NewStyle().Foreground(th.textSoft).Bold(true)
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
	return renderSlashMenuForTheme(state, defaultTheme)
}

func renderSlashMenuForTheme(state slashMenuState, th theme) string {
	rows := visibleSlashMenuRows(state)
	valueWidth := slashMenuValueWidth(rows)
	lines := make([]string, 0, len(rows))
	selectedStart := max(0, state.Selected-(maxSlashMenuRows/2))
	if selectedStart+len(rows) > len(state.Rows) {
		selectedStart = len(state.Rows) - len(rows)
	}
	for idx, row := range rows {
		actualIndex := selectedStart + idx
		prefix := " "
		valueColor := th.textMuted
		descColor := th.textMuted
		if actualIndex == state.Selected {
			prefix = ">"
			valueColor = th.accentSoft
			descColor = th.textSoft
		}
		currentBadge := ""
		if row.Current {
			currentColor := th.successSoft
			if actualIndex == state.Selected {
				currentColor = th.success
			}
			currentBadge = lipgloss.NewStyle().Foreground(currentColor).Render("[current]") + " "
		}
		lines = append(lines,
			lipgloss.JoinHorizontal(
				lipgloss.Left,
				lipgloss.NewStyle().Foreground(th.textMuted).Render(prefix),
				" ",
				lipgloss.NewStyle().Foreground(valueColor).Render(padRight(slashDisplayValue(row), valueWidth)),
				currentBadge,
				renderSlashDescription(row.Description, descColor, th),
			),
		)
	}
	return lipgloss.JoinVertical(lipgloss.Left, lines...)
}

func slashMenuValueWidth(rows []slashSuggestion) int {
	width := 16
	for _, row := range rows {
		width = max(width, lipgloss.Width(slashDisplayValue(row)))
	}
	return min(width, 28)
}

func slashDisplayValue(row slashSuggestion) string {
	if strings.TrimSpace(row.DisplayValue) != "" {
		return row.DisplayValue
	}
	return row.Value
}

func renderSlashDescription(description string, descColor lipgloss.TerminalColor, th theme) string {
	const readyBadge = "[✓]"
	if !strings.Contains(description, readyBadge) {
		return lipgloss.NewStyle().Foreground(descColor).Render(description)
	}
	parts := strings.SplitN(description, readyBadge, 2)
	badge := lipgloss.NewStyle().Foreground(th.successSoft).Render(readyBadge)
	rendered := lipgloss.NewStyle().Foreground(descColor).Render(parts[0]) + badge
	if len(parts) == 2 && parts[1] != "" {
		rendered += lipgloss.NewStyle().Foreground(descColor).Render(parts[1])
	}
	return rendered
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
		parts := []string{displayModelName(vm.Model)}
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
	th := themeForPermissionPreset(vm.PermissionPreset)
	previewStyle := lipgloss.NewStyle().
		BorderLeft(true).
		BorderForeground(th.border).
		PaddingLeft(1)
	sectionGap := ""
	var sections []string
	if len(vm.QueuedNext) > 0 {
		sections = append(sections, renderQueuedInputSection(
			"After current tool phase",
			"Esc sends now",
			vm.QueuedNext,
			width,
			th,
		))
		sectionGap = "\n"
	}
	if len(vm.QueuedLater) > 0 {
		sections = append(sections, renderQueuedInputSection(
			"At end of turn",
			"",
			vm.QueuedLater,
			width,
			th,
		))
	}
	return previewStyle.Render(strings.Join(sections, sectionGap))
}

func renderQueuedInputSection(title string, hint string, prompts []string, width int, th theme) string {
	header := lipgloss.NewStyle().Foreground(th.textMuted).Render(title)
	count := lipgloss.NewStyle().Foreground(th.accentSoft).Render(
		fmt.Sprintf("%d queued", len(prompts)),
	)
	headerLine := lipgloss.JoinHorizontal(lipgloss.Left, header, "  ", count)
	if hint != "" {
		headerLine = lipgloss.JoinHorizontal(
			lipgloss.Left,
			headerLine,
			"  ",
			lipgloss.NewStyle().Foreground(th.textMuted).Render(hint),
		)
	}
	lines := []string{headerLine}
	maxPrompts := minInt(len(prompts), 3)
	for i := 0; i < maxPrompts; i++ {
		lines = append(lines, renderQueuedPromptLine(prompts[i], width, th))
	}
	if len(prompts) > maxPrompts {
		lines = append(lines, lipgloss.NewStyle().Foreground(th.textMuted).Render("  …"))
	}
	return strings.Join(lines, "\n")
}

func renderQueuedPromptLine(prompt string, width int, th theme) string {
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
	return lipgloss.NewStyle().Foreground(th.textSoft).Render("  ↳ " + firstLine)
}

func terminalColorRGB(color lipgloss.TerminalColor) (uint8, uint8, uint8) {
	complete, ok := color.(lipgloss.CompleteColor)
	if !ok {
		return 0x3d, 0x35, 0x20
	}
	hex := strings.TrimPrefix(complete.TrueColor, "#")
	if len(hex) != 6 {
		return 0x3d, 0x35, 0x20
	}
	var r, g, b uint8
	if _, err := fmt.Sscanf(hex, "%02x%02x%02x", &r, &g, &b); err != nil {
		return 0x3d, 0x35, 0x20
	}
	return r, g, b
}

func minInt(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func buildIdleFooterText(vm viewModel) string {
	parts := []string{
		displayModelName(vm.Model),
		displayPath(vm.WorkspaceRoot),
	}
	if vm.Thinking != "" {
		parts = append(parts, fmt.Sprintf("thinking=%s", vm.Thinking))
	}
	if usage := buildUsageFooterText(vm, false); usage != "" {
		parts = append(parts, usage)
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
		displayModelName(vm.Model),
		displayPath(vm.WorkspaceRoot),
	}
	if vm.Thinking != "" {
		parts = append(parts, fmt.Sprintf("thinking=%s", vm.Thinking))
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
