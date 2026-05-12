package app

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"jaca/internal/jaca/rpc"
)

type toolEntry struct {
	toolName           string
	displayLabel       string
	preview            string
	outcome            string
	message            string
	duration           string
	groupKind          string
	activity           *rpc.ToolActivity
	detailLines        []string
	resultLines        []string
	resultTruncated    bool
	resultOmittedLines int
	resultHeadCount    int
	operationalMiss    bool
}

type toolGroup struct {
	phase   string
	order   []string
	entries map[string]*toolEntry
}

func newToolGroup(phase string) *toolGroup {
	return &toolGroup{
		phase:   phase,
		entries: map[string]*toolEntry{},
	}
}

func (g *toolGroup) accepts(event rpc.RunEvent) bool {
	return g.phase == buildToolPhase(event.ToolName, event.Activity)
}

func (g *toolGroup) start(event rpc.RunEvent) {
	g.order = append(g.order, event.ToolCallID)
	g.entries[event.ToolCallID] = &toolEntry{
		toolName:     event.ToolName,
		displayLabel: buildToolDisplayLabel(event.ToolName, event.Activity),
		preview:      buildToolPreview(event.ToolName, event.Args, event.ArgsValid, event.Activity),
		groupKind:    buildToolGroupKind(event.Activity),
		activity:     event.Activity,
	}
}

func (g *toolGroup) finish(event rpc.RunEvent) bool {
	entry := g.entries[event.ToolCallID]
	if entry == nil {
		return false
	}
	resultState := toolResultState(event.Result)
	entry.operationalMiss = resultState == "operational_miss"
	switch resultState {
	case "operational_miss":
		entry.outcome = ""
	case "denied":
		entry.outcome = "denied"
	default:
		entry.outcome = "ok"
	}
	entry.resultLines = nil
	entry.resultTruncated = false
	entry.detailLines = nil
	if entry.preview == "" {
		entry.message = buildToolSummary(event.Activity, "")
	} else {
		entry.message = ""
	}
	entry.duration = buildToolDuration(event.Activity)
	entry.detailLines = buildToolDetailLines(event.Activity)
	if len(entry.detailLines) == 0 {
		entry.resultLines, entry.resultTruncated, entry.resultOmittedLines, entry.resultHeadCount = extractToolResultLines(event.Result)
	}
	if len(entry.detailLines) == 0 && len(entry.resultLines) == 0 && entry.message == "" {
		entry.message = buildToolSummary(event.Activity, "")
	}
	entry.groupKind = buildToolGroupKind(event.Activity)
	if event.Activity != nil {
		entry.activity = event.Activity
		entry.displayLabel = buildToolDisplayLabel(entry.toolName, event.Activity)
	}
	return true
}

func (g *toolGroup) update(event rpc.RunEvent) bool {
	entry := g.entries[event.ToolCallID]
	if entry == nil {
		return false
	}
	entry.outcome = "running"
	entry.message = buildToolSummary(event.Activity, "")
	entry.duration = buildToolDuration(event.Activity)
	entry.groupKind = buildToolGroupKind(event.Activity)
	if event.Activity != nil {
		entry.activity = event.Activity
		entry.displayLabel = buildToolDisplayLabel(entry.toolName, event.Activity)
	}
	entry.detailLines = buildToolDetailLines(event.Activity)
	entry.resultLines = nil
	entry.resultTruncated = false
	entry.resultOmittedLines = 0
	entry.resultHeadCount = 0
	if len(entry.detailLines) == 0 {
		entry.resultLines, entry.resultTruncated, entry.resultOmittedLines, entry.resultHeadCount = extractToolResultLines(event.Partial)
	}
	return true
}

func (g *toolGroup) fail(event rpc.RunEvent) bool {
	entry := g.entries[event.ToolCallID]
	if entry == nil {
		return false
	}
	entry.outcome = "error"
	entry.message = buildToolSummary(event.Activity, event.Message)
	entry.duration = buildToolDuration(event.Activity)
	entry.groupKind = buildToolGroupKind(event.Activity)
	if event.Activity != nil {
		entry.activity = event.Activity
		entry.displayLabel = buildToolDisplayLabel(entry.toolName, event.Activity)
	}
	entry.detailLines = nil
	entry.resultLines = nil
	entry.resultTruncated = false
	return true
}

func (g *toolGroup) render(motionTick int) (string, string) {
	if isExplorationGroup(g.order, g.entries) {
		return renderExplorationGroup(g.order, g.entries, motionTick)
	}

	var plain strings.Builder
	var rendered strings.Builder
	prevHadDetail := false
	for _, toolCallID := range g.order {
		entry := g.entries[toolCallID]
		if prevHadDetail {
			plain.WriteByte('\n')
			rendered.WriteByte('\n')
		}
		plain.WriteString(formatToolActivityLine(entry))
		rendered.WriteString(renderToolActivityLine(entry, motionTick))
		for _, line := range entry.detailLines {
			plain.WriteString(line + "\n")
			rendered.WriteString(styleToolDetailLine(line) + "\n")
		}
		resultColor := defaultTheme.textMuted
		if entry.operationalMiss || entry.outcome == "denied" {
			resultColor = defaultTheme.errSoft
		}
		for idx, line := range entry.resultLines {
			prefix := "    "
			if idx == 0 {
				prefix = "  └ "
			}
			plain.WriteString(prefix + line + "\n")
			rendered.WriteString(lipgloss.NewStyle().Foreground(resultColor).Render(prefix+line) + "\n")
			if entry.resultTruncated && idx == entry.resultHeadCount-1 {
				truncMsg := "    ..."
				if entry.resultOmittedLines > 0 {
					truncMsg = fmt.Sprintf("    ... +%d more lines", entry.resultOmittedLines)
				}
				plain.WriteString(truncMsg + "\n")
				rendered.WriteString(lipgloss.NewStyle().Foreground(resultColor).Render(truncMsg) + "\n")
			}
		}
		prevHadDetail = len(entry.detailLines) > 0 || len(entry.resultLines) > 0 || entry.resultTruncated || (entry.outcome == "error" && entry.message != "")
	}
	return plain.String(), rendered.String()
}

const (
	maxToolResultLines     = 6
	maxToolResultLineChars = 160
	maxEditDiffLines       = 12
	maxEditDiffLineChars   = 160
)

var (
	editHunkRe         = regexp.MustCompile(`^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@`)
	diffRemovedRe      = regexp.MustCompile(`^\d+ - `)
	diffAddedRe        = regexp.MustCompile(`^\d+ \+ `)
	teachingCodeLineRe = regexp.MustCompile(`^│\s+(\d+)\s+│\s?(.*)$`)
)

func formatToolActivityLine(entry *toolEntry) string {
	if entry.outcome == "error" {
		return formatToolErrorActivityLine(entry)
	}
	head := "● " + entry.toolName
	if entry.preview != "" {
		head += "  " + entry.preview
	}
	switch {
	case entry.outcome != "" && entry.duration != "" && entry.message == "":
		return fmt.Sprintf("%s  %s  %s\n", head, entry.outcome, entry.duration)
	case entry.outcome != "" && entry.message != "" && entry.duration != "":
		return fmt.Sprintf("%s  %s  %s  %s\n", head, entry.outcome, entry.message, entry.duration)
	case entry.outcome != "" && entry.message != "":
		return fmt.Sprintf("%s  %s  %s\n", head, entry.outcome, entry.message)
	case entry.outcome != "":
		return fmt.Sprintf("%s  %s\n", head, entry.outcome)
	case entry.duration != "" && entry.message != "":
		return fmt.Sprintf("%s  %s  %s\n", head, entry.message, entry.duration)
	case entry.duration != "":
		return fmt.Sprintf("%s  %s\n", head, entry.duration)
	case entry.message != "":
		return fmt.Sprintf("%s  %s\n", head, entry.message)
	default:
		return head + "\n"
	}
}

func formatToolErrorActivityLine(entry *toolEntry) string {
	head := "× " + toolActivityTitle(entry) + " failed"
	if entry.duration != "" {
		head += " " + entry.duration
	}
	if entry.message == "" {
		return head + "\n"
	}
	return head + "\n  └ " + entry.message + "\n"
}

func toolActivityTitle(entry *toolEntry) string {
	if entry.displayLabel != "" {
		return entry.displayLabel
	}
	return capitalizeFirst(entry.toolName)
}

func renderToolActivityLine(entry *toolEntry, motionTick int) string {
	if entry.outcome == "error" {
		return renderToolErrorActivityLine(entry)
	}
	var markerColor lipgloss.TerminalColor
	switch {
	case entry.outcome == "ok" && entry.groupKind == "exploration":
		markerColor = defaultTheme.textMuted
	case entry.outcome == "ok":
		markerColor = defaultTheme.successSoft
	case entry.outcome == "error":
		markerColor = defaultTheme.err
	case entry.outcome == "denied":
		markerColor = defaultTheme.errSoft
	case entry.outcome == "":
		markerColor = breathingMarkerColor(motionTick)
	default:
		markerColor = defaultTheme.accent
	}
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(markerColor).Render("● "))
	b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(entry.toolName))
	if entry.preview != "" {
		b.WriteString("  ")
		previewColor := defaultTheme.text
		if entry.groupKind == "exploration" && entry.outcome != "error" {
			previewColor = defaultTheme.textSoft
		}
		b.WriteString(lipgloss.NewStyle().Foreground(previewColor).Render(entry.preview))
	}
	if entry.outcome != "" {
		b.WriteString("  ")
		color := toolOutcomeColor(entry.outcome)
		b.WriteString(lipgloss.NewStyle().Foreground(color).Render(entry.outcome))
	}
	if entry.message != "" {
		b.WriteString("  ")
		color := defaultTheme.textMuted
		if entry.outcome == "error" {
			color = defaultTheme.err
		} else if entry.outcome == "denied" {
			color = defaultTheme.errSoft
		} else if entry.outcome == "running" {
			color = defaultTheme.accentSoft
		}
		b.WriteString(lipgloss.NewStyle().Foreground(color).Render(entry.message))
	}
	if entry.duration != "" {
		b.WriteString("  ")
		b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(entry.duration))
	}
	b.WriteByte('\n')
	return b.String()
}

func renderToolErrorActivityLine(entry *toolEntry) string {
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.err).Render("× "))
	b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.err).Render(toolActivityTitle(entry)))
	b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.err).Render(" failed"))
	if entry.duration != "" {
		b.WriteString(" ")
		b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(entry.duration))
	}
	b.WriteByte('\n')
	if entry.message != "" {
		b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render("  └ " + entry.message))
		b.WriteByte('\n')
	}
	return b.String()
}

func isExplorationGroup(order []string, entries map[string]*toolEntry) bool {
	if len(order) == 0 {
		return false
	}
	for _, id := range order {
		if entries[id].groupKind != "exploration" {
			return false
		}
	}
	return true
}

func isExplorationComplete(order []string, entries map[string]*toolEntry) bool {
	for _, id := range order {
		e := entries[id]
		if e.outcome != "ok" && e.outcome != "error" && e.outcome != "" {
			return false
		}
		if e.outcome == "" && !e.operationalMiss {
			return false
		}
	}
	return true
}

func explorationGroupState(order []string, entries map[string]*toolEntry, complete bool) string {
	if !complete {
		return ""
	}
	for _, id := range order {
		if entries[id].outcome == "error" {
			return "error"
		}
	}
	for _, id := range order {
		if entries[id].outcome == "denied" {
			return "denied"
		}
	}
	for _, id := range order {
		if entries[id].operationalMiss {
			return "partial"
		}
	}
	return "ok"
}

func explorationGroupDuration(order []string, entries map[string]*toolEntry) string {
	totalMS := 0
	for _, id := range order {
		entry := entries[id]
		if entry.activity == nil || entry.activity.DurationMS == nil || *entry.activity.DurationMS <= 0 {
			continue
		}
		totalMS += *entry.activity.DurationMS
	}
	if totalMS == 0 {
		return ""
	}
	return formatToolDurationMS(totalMS)
}

const (
	maxExplorationLines    = 6
	maxExplorationArgsLen  = 96
	maxExplorationQueryLen = 40
)

type explorationLine struct {
	label           string
	args            string
	outcome         string
	message         string
	operationalMiss bool
}

func coalesceExplorationEntries(order []string, entries map[string]*toolEntry) []explorationLine {
	var lines []explorationLine
	var pendingLabel string
	var pendingArgs []string

	flush := func() {
		if pendingLabel != "" && len(pendingArgs) > 0 {
			seen := map[string]bool{}
			var unique []string
			for _, a := range pendingArgs {
				if !seen[a] {
					seen[a] = true
					unique = append(unique, a)
				}
			}
			lines = append(lines, explorationLine{
				label: pendingLabel,
				args:  strings.Join(unique, ", "),
			})
		}
		pendingLabel = ""
		pendingArgs = nil
	}

	for _, id := range order {
		entry := entries[id]
		label := entry.displayLabel
		if label == "" {
			label = capitalizeFirst(entry.toolName)
		}

		if entry.operationalMiss || entry.outcome == "error" || entry.outcome == "denied" {
			message := entry.message
			if message == "" && len(entry.resultLines) > 0 {
				message = entry.resultLines[0]
			}
			flush()
			lines = append(lines, explorationLine{
				label:           label,
				args:            explorationEntryArgs(entry),
				outcome:         entry.outcome,
				message:         message,
				operationalMiss: entry.operationalMiss,
			})
			continue
		}

		switch entry.toolName {
		case "read", "ls":
			path := explorationShortPath(entry)
			if label == pendingLabel {
				pendingArgs = append(pendingArgs, path)
			} else {
				flush()
				pendingLabel = label
				pendingArgs = []string{path}
			}
		case "grep", "find":
			flush()
			lines = append(lines, explorationLine{
				label: label,
				args:  explorationSearchArgs(entry),
			})
		default:
			flush()
			lines = append(lines, explorationLine{label: label, args: explorationShortPath(entry)})
		}
	}
	flush()

	if len(lines) > maxExplorationLines {
		headCount := 3
		tailCount := 2
		omitted := len(lines) - headCount - tailCount
		tail := make([]explorationLine, tailCount)
		copy(tail, lines[len(lines)-tailCount:])
		lines = append(lines[:headCount],
			explorationLine{label: fmt.Sprintf("... +%d more", omitted)},
		)
		lines = append(lines, tail...)
	}
	return lines
}

func explorationShortPath(entry *toolEntry) string {
	if entry.activity != nil && entry.activity.Details != nil {
		if sp, ok := entry.activity.Details["short_path"].(string); ok && sp != "" {
			return sp
		}
		if p, ok := entry.activity.Details["path"].(string); ok && p != "" {
			return shortenPathFallback(p)
		}
	}
	return shortenPathFallback(entry.preview)
}

func explorationSearchArgs(entry *toolEntry) string {
	query := ""
	dir := ""
	if entry.activity != nil && entry.activity.Details != nil {
		if q, ok := entry.activity.Details["pattern"].(string); ok {
			query = q
		}
		if sp, ok := entry.activity.Details["short_path"].(string); ok && sp != "" {
			dir = sp
		} else if p, ok := entry.activity.Details["path"].(string); ok && p != "" {
			dir = shortenPathFallback(p)
		}
	}
	if query == "" {
		return shortenPathFallback(entry.preview)
	}
	if len(query) > maxExplorationQueryLen {
		query = query[:maxExplorationQueryLen-3] + "..."
	}
	if dir != "" && dir != "." {
		return query + " in " + dir
	}
	return query
}

func explorationEntryArgs(entry *toolEntry) string {
	switch entry.toolName {
	case "grep", "find":
		return explorationSearchArgs(entry)
	default:
		return explorationShortPath(entry)
	}
}

func shortenPathFallback(path string) string {
	if path == "" {
		return ""
	}
	if slash := strings.LastIndex(path, "/"); slash >= 0 && !strings.Contains(path, " ") {
		return path[slash+1:]
	}
	return path
}

func capitalizeFirst(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

func buildToolDisplayLabel(toolName string, activity *rpc.ToolActivity) string {
	if activity != nil && activity.DisplayLabel != nil && *activity.DisplayLabel != "" {
		return *activity.DisplayLabel
	}
	return capitalizeFirst(toolName)
}

func buildToolPhase(toolName string, activity *rpc.ToolActivity) string {
	if buildToolGroupKind(activity) == "exploration" {
		return "exploration"
	}
	switch toolName {
	case "shell":
		return "execution"
	case "edit", "write", "apply_patch":
		return "editing"
	default:
		return "other"
	}
}

func renderExplorationGroup(order []string, entries map[string]*toolEntry, motionTick int) (string, string) {
	complete := isExplorationComplete(order, entries)
	state := explorationGroupState(order, entries, complete)
	duration := explorationGroupDuration(order, entries)

	marker := "● "
	headerLabel := fmt.Sprintf("Read/Searched (%d)", len(order))
	if state != "" {
		headerLabel += " " + state
	}
	if duration != "" {
		headerLabel += " " + duration
	}
	var markerColor lipgloss.TerminalColor
	if state == "error" {
		marker = "× "
		markerColor = defaultTheme.err
	} else if state == "denied" {
		markerColor = defaultTheme.errSoft
	} else if complete {
		markerColor = defaultTheme.textMuted
	} else {
		markerColor = breathingMarkerColor(motionTick)
	}

	headerPlain := marker + headerLabel + "\n"
	headerLabelStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	if !complete {
		headerLabelStyle = lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Bold(true)
	}
	headerRendered := lipgloss.NewStyle().Foreground(markerColor).Render(marker) +
		headerLabelStyle.Render(headerLabel) + "\n"
	if state == "denied" {
		headerRendered = lipgloss.NewStyle().Foreground(markerColor).Render(marker) +
			lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Bold(true).Render(headerLabel) + "\n"
	}

	var plain, rendered strings.Builder
	plain.WriteString(headerPlain)
	rendered.WriteString(headerRendered)

	labelStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	if !complete {
		labelStyle = lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
	}
	argsStyle := lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
	dimStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)

	lines := coalesceExplorationEntries(order, entries)
	for idx, line := range lines {
		prefix := "    "
		if idx == 0 {
			prefix = "  └ "
		}

		if strings.HasPrefix(line.label, "...") {
			plain.WriteString(prefix + line.label + "\n")
			rendered.WriteString(dimStyle.Render(prefix+line.label) + "\n")
			continue
		}

		plainLine := prefix + line.label
		renderedLine := dimStyle.Render(prefix) + labelStyle.Render(line.label)
		args := truncateInline(line.args, maxExplorationArgsLen)
		if args != "" {
			plainLine += " " + args
			renderedLine += " " + argsStyle.Render(args)
		}
		if line.operationalMiss {
			if line.message != "" {
				plainLine += "  " + line.message
				renderedLine += "  " + lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render(line.message)
			}
		} else if line.outcome == "denied" {
			plainLine += "  denied"
			renderedLine += "  " + lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render("denied")
			if line.message != "" {
				plainLine += "  " + line.message
				renderedLine += "  " + lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render(line.message)
			}
		} else if line.outcome == "error" {
			plainLine += "  error"
			renderedLine += "  " + lipgloss.NewStyle().Foreground(defaultTheme.err).Render("error")
			if line.message != "" {
				plainLine += "  " + line.message
				renderedLine += "  " + lipgloss.NewStyle().Foreground(defaultTheme.err).Render(line.message)
			}
		}

		plain.WriteString(plainLine + "\n")
		rendered.WriteString(renderedLine + "\n")
	}

	return plain.String(), rendered.String()
}

func toolOutcomeColor(outcome string) lipgloss.TerminalColor {
	switch outcome {
	case "ok":
		return defaultTheme.successSoft
	case "denied":
		return defaultTheme.errSoft
	case "error":
		return defaultTheme.err
	case "running":
		return defaultTheme.accentSoft
	default:
		return defaultTheme.textMuted
	}
}

func styleToolDetailLine(line string) string {
	trimmed := strings.TrimLeft(line, " │")
	switch {
	case strings.HasPrefix(trimmed, "Concept") ||
		strings.HasPrefix(trimmed, "Relationships") ||
		strings.HasPrefix(trimmed, "Code evidence"):
		return lipgloss.NewStyle().Foreground(defaultTheme.accentSoft).Bold(true).Render(line)
	case strings.HasPrefix(trimmed, "Evidence "):
		return lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Bold(true).Render(line)
	case strings.HasPrefix(trimmed, "1. ") ||
		strings.HasPrefix(trimmed, "2. ") ||
		strings.HasPrefix(trimmed, "3. ") ||
		strings.HasPrefix(trimmed, "4. ") ||
		strings.HasPrefix(trimmed, "5. "):
		return lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(line)
	case teachingCodeLineRe.MatchString(strings.TrimLeft(line, " ")):
		return styleTeachingCodeLine(line)
	case diffRemovedRe.MatchString(trimmed):
		return lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render(line)
	case diffAddedRe.MatchString(trimmed):
		return lipgloss.NewStyle().Foreground(defaultTheme.successSoft).Render(line)
	case strings.HasPrefix(trimmed, "@@ "):
		return lipgloss.NewStyle().Foreground(defaultTheme.accentSoft).Render(line)
	case strings.HasPrefix(trimmed, "Update("):
		return lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Bold(true).Render(line)
	case strings.HasPrefix(trimmed, "Added ") || strings.HasPrefix(trimmed, "removed "):
		return lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(line)
	default:
		return lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(line)
	}
}

func styleTeachingCodeLine(line string) string {
	trimmed := strings.TrimLeft(line, " ")
	indent := line[:len(line)-len(trimmed)]
	match := teachingCodeLineRe.FindStringSubmatch(trimmed)
	if match == nil {
		return lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(line)
	}
	gutterStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	codeStyle := teachingCodeStyle(match[2])
	return indent +
		gutterStyle.Render("│ "+match[1]+" │ ") +
		codeStyle.Render(match[2])
}

func teachingCodeStyle(code string) lipgloss.Style {
	trimmed := strings.TrimSpace(code)
	switch {
	case trimmed == "":
		return lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	case strings.HasPrefix(trimmed, "//") ||
		strings.HasPrefix(trimmed, "#") ||
		strings.HasPrefix(trimmed, "/*") ||
		strings.HasPrefix(trimmed, "*"):
		return lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	case strings.HasPrefix(trimmed, "func ") ||
		strings.HasPrefix(trimmed, "def ") ||
		strings.HasPrefix(trimmed, "class ") ||
		strings.HasPrefix(trimmed, "type ") ||
		strings.HasPrefix(trimmed, "const ") ||
		strings.HasPrefix(trimmed, "var "):
		return lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
	case strings.Contains(trimmed, "return ") ||
		strings.Contains(trimmed, "raise ") ||
		strings.Contains(trimmed, "yield "):
		return lipgloss.NewStyle().Foreground(defaultTheme.successSoft)
	default:
		return lipgloss.NewStyle().Foreground(defaultTheme.text)
	}
}

func buildToolPreview(toolName string, args map[string]any, argsValid *bool, activity *rpc.ToolActivity) string {
	if activity != nil && activity.Title != "" {
		normalized := strings.Join(strings.Fields(activity.Title), " ")
		if normalized != toolName {
			prefix := toolName + " "
			if strings.HasPrefix(normalized, prefix) {
				return normalized[len(prefix):]
			}
			return normalized
		}
	}
	if activity != nil && activity.Details != nil {
		switch toolName {
		case "read", "write", "edit", "ls":
			if sp, ok := activity.Details["short_path"].(string); ok && sp != "" {
				return truncateInline(sp, 56)
			}
			if p, ok := activity.Details["path"].(string); ok && p != "" {
				return truncateInline(shortenPathFallback(p), 56)
			}
		case "grep", "find":
			if pattern, ok := activity.Details["pattern"].(string); ok && pattern != "" {
				return truncateInline(pattern, 56)
			}
		}
	}
	if argsValid != nil && !*argsValid {
		return ""
	}
	switch toolName {
	case "shell":
		if command, ok := args["command"].(string); ok {
			return truncateInline(command, 56)
		}
	case "read", "write", "edit", "ls":
		if path, ok := args["path"].(string); ok {
			return truncateInline(path, 56)
		}
	case "grep", "find":
		if pattern, ok := args["pattern"].(string); ok {
			return truncateInline(pattern, 56)
		}
	}
	return ""
}

func buildToolSummary(activity *rpc.ToolActivity, fallback string) string {
	if activity != nil && activity.Summary != nil && *activity.Summary != "" {
		return strings.Join(strings.Fields(*activity.Summary), " ")
	}
	return fallback
}

func buildToolDuration(activity *rpc.ToolActivity) string {
	if activity == nil || activity.DurationMS == nil || *activity.DurationMS < 0 {
		return ""
	}
	return formatToolDurationMS(*activity.DurationMS)
}

func formatToolDurationMS(durationMS int) string {
	if durationMS < 1000 {
		return fmt.Sprintf("%dms", durationMS)
	}
	return fmt.Sprintf("%.1fs", float64(durationMS)/1000.0)
}

func buildToolDetailLines(activity *rpc.ToolActivity) []string {
	if activity == nil || activity.Details == nil {
		return nil
	}
	details := activity.Details
	if kind, _ := details["kind"].(string); kind == "mcp" {
		if wrappedDetails, ok := details["wrapped_details"].(map[string]any); ok {
			details = wrappedDetails
			copied := *activity
			copied.Details = wrappedDetails
			activity = &copied
		}
	}
	kind, _ := details["kind"].(string)
	switch kind {
	case "edit":
		return buildEditDetailLines(activity)
	case "subagent":
		return buildSubagentDetailLines(activity)
	case "teaching_packet":
		return buildTeachingPacketDetailLines(activity)
	default:
		return nil
	}
}

func buildEditDetailLines(activity *rpc.ToolActivity) []string {
	path, _ := activity.Details["path"].(string)
	if path == "" {
		return nil
	}
	lines := []string{fmt.Sprintf("  Update(%s)", path)}
	added := intFromAny(activity.Details["added_lines"])
	removed := intFromAny(activity.Details["removed_lines"])
	if added != nil || removed != nil {
		summary := []string{}
		if added != nil {
			noun := "lines"
			if *added == 1 {
				noun = "line"
			}
			summary = append(summary, fmt.Sprintf("Added %d %s", *added, noun))
		}
		if removed != nil {
			noun := "lines"
			if *removed == 1 {
				noun = "line"
			}
			summary = append(summary, fmt.Sprintf("removed %d %s", *removed, noun))
		}
		lines = append(lines, "  │ "+strings.Join(summary, ", "))
	}
	diff, _ := activity.Details["diff"].(string)
	if diff != "" {
		lines = append(lines, renderEditDiffLines(diff)...)
	}
	return lines
}

func buildSubagentDetailLines(activity *rpc.ToolActivity) []string {
	rawLines := stringSliceFromAny(activity.Details["preview_lines"])
	if len(rawLines) == 0 {
		return nil
	}
	previewTerminal, _ := activity.Details["preview_terminal"].(bool)
	lines := make([]string, 0, len(rawLines))
	for idx, line := range rawLines {
		prefix := "  │ "
		if previewTerminal && idx == len(rawLines)-1 {
			prefix = "  └ "
		}
		lines = append(lines, prefix+truncateDisplayLine(line, maxToolResultLineChars))
	}
	return lines
}

func buildTeachingPacketDetailLines(activity *rpc.ToolActivity) []string {
	lines := []string{}

	if concept, ok := activity.Details["concept"].(string); ok && concept != "" {
		lines = append(
			lines,
			"  Concept",
			"  │ "+truncateDisplayLine(concept, maxToolResultLineChars),
		)
	}

	if rawRelationships, ok := activity.Details["relationships"].([]any); ok {
		relationshipLines := []string{}
		for _, rawRelationship := range rawRelationships {
			relationship, ok := rawRelationship.(map[string]any)
			if !ok {
				continue
			}
			statement, _ := relationship["statement"].(string)
			if statement == "" {
				continue
			}
			relationshipLines = append(
				relationshipLines,
				fmt.Sprintf(
					"  │ %d. %s",
					len(relationshipLines)+1,
					truncateDisplayLine(statement, maxToolResultLineChars),
				),
			)
		}
		if len(relationshipLines) > 0 {
			lines = append(lines, "  Relationships")
			lines = append(lines, relationshipLines...)
		}
	}

	rawSnippets, ok := activity.Details["snippets"].([]any)
	if !ok || len(rawSnippets) == 0 {
		if len(lines) == 0 {
			return nil
		}
		return lines
	}
	if len(lines) > 0 {
		lines = append(lines, "  Code evidence")
	}
	snippetLines := []string{}
	for snippetIndex, rawSnippet := range rawSnippets {
		snippet, ok := rawSnippet.(map[string]any)
		if !ok {
			continue
		}
		path, _ := snippet["path"].(string)
		startLine := intFromAny(snippet["start_line"])
		endLine := intFromAny(snippet["end_line"])
		text, _ := snippet["text"].(string)
		if path == "" || startLine == nil || endLine == nil || text == "" {
			continue
		}
		snippetLines = append(
			snippetLines,
			fmt.Sprintf(
				"  Evidence %d  %s:%d-%d",
				snippetIndex+1,
				path,
				*startLine,
				*endLine,
			),
		)
		lineNumberWidth := len(fmt.Sprintf("%d", *endLine))
		for idx, rawLine := range strings.Split(text, "\n") {
			lineNumber := *startLine + idx
			snippetLines = append(
				snippetLines,
				fmt.Sprintf(
					"  │ %*d │ %s",
					lineNumberWidth,
					lineNumber,
					truncateDisplayLine(rawLine, maxToolResultLineChars),
				),
			)
		}
	}
	if len(snippetLines) == 0 {
		if len(lines) == 0 {
			return nil
		}
		return lines
	}
	lines = append(lines, snippetLines...)
	if len(lines) == 0 {
		return nil
	}
	return lines
}

func renderEditDiffLines(diff string) []string {
	rows := parseEditDiffRows(diff)
	if len(rows) > maxEditDiffLines {
		rows = append(rows[:maxEditDiffLines], "  │ ...")
	}
	for i := range rows {
		rows[i] = truncateDisplayLine(rows[i], maxEditDiffLineChars)
	}
	return rows
}

func parseEditDiffRows(diff string) []string {
	lines := []string{}
	oldLine := 0
	newLine := 0
	for _, raw := range strings.Split(diff, "\n") {
		if strings.HasPrefix(raw, "--- ") || strings.HasPrefix(raw, "+++ ") || strings.HasPrefix(raw, "\\ No newline") {
			continue
		}
		if match := editHunkRe.FindStringSubmatch(raw); match != nil {
			oldLine = atoiSafe(match[1])
			newLine = atoiSafe(match[2])
			lines = append(lines, "  │ "+raw)
			continue
		}
		switch {
		case strings.HasPrefix(raw, " "):
			lines = append(lines, fmt.Sprintf("  │ %d   %s", newLine, raw[1:]))
			oldLine++
			newLine++
		case strings.HasPrefix(raw, "-"):
			lines = append(lines, fmt.Sprintf("  │ %d - %s", oldLine, raw[1:]))
			oldLine++
		case strings.HasPrefix(raw, "+"):
			lines = append(lines, fmt.Sprintf("  │ %d + %s", newLine, raw[1:]))
			newLine++
		}
	}
	return lines
}

func extractToolResultLines(result any) ([]string, bool, int, int) {
	switch value := result.(type) {
	case string:
		lines := strings.Split(strings.TrimSpace(value), "\n")
		return truncateLines(lines, maxToolResultLines)
	case map[string]any:
		if output, ok := value["output"].(string); ok {
			return truncateLines(strings.Split(strings.TrimSpace(output), "\n"), maxToolResultLines)
		}
		if message, ok := value["message"].(string); ok {
			return truncateLines(strings.Split(strings.TrimSpace(message), "\n"), maxToolResultLines)
		}
	}
	return nil, false, 0, 0
}

func truncateLines(lines []string, limit int) ([]string, bool, int, int) {
	filtered := make([]string, 0, len(lines))
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		filtered = append(filtered, truncateDisplayLine(line, maxToolResultLineChars))
	}
	if len(filtered) > limit {
		tailCount := (limit - 1) / 2
		headCount := limit - tailCount - 1
		omitted := len(filtered) - headCount - tailCount
		result := make([]string, 0, headCount+tailCount)
		result = append(result, filtered[:headCount]...)
		result = append(result, filtered[len(filtered)-tailCount:]...)
		return result, true, omitted, headCount
	}
	return filtered, false, 0, 0
}

func toolResultState(result any) string {
	m, ok := result.(map[string]any)
	if !ok {
		return ""
	}
	if outcome, ok := m["outcome"].(string); ok && outcome == "denied" {
		return "denied"
	}
	flag, ok := m["ok"].(bool)
	if ok && !flag {
		return "operational_miss"
	}
	return ""
}

func intFromAny(value any) *int {
	switch v := value.(type) {
	case float64:
		n := int(v)
		return &n
	case int:
		n := v
		return &n
	}
	return nil
}

func stringSliceFromAny(value any) []string {
	switch v := value.(type) {
	case []string:
		lines := make([]string, 0, len(v))
		for _, line := range v {
			if strings.TrimSpace(line) == "" {
				continue
			}
			lines = append(lines, strings.Join(strings.Fields(line), " "))
		}
		return lines
	case []any:
		lines := make([]string, 0, len(v))
		for _, raw := range v {
			line, ok := raw.(string)
			if !ok || strings.TrimSpace(line) == "" {
				continue
			}
			lines = append(lines, strings.Join(strings.Fields(line), " "))
		}
		return lines
	default:
		return nil
	}
}
