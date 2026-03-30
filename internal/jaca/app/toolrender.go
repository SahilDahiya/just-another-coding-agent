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
	preview            string
	outcome            string
	message            string
	duration           string
	groupKind          string
	detailLines        []string
	resultLines        []string
	resultTruncated    bool
	resultOmittedLines int
	resultHeadCount    int
	operationalMiss    bool
}

type toolGroup struct {
	index   int
	order   []string
	entries map[string]*toolEntry
}

const (
	maxToolResultLines     = 6
	maxToolResultLineChars = 160
	maxEditDiffLines       = 12
	maxEditDiffLineChars   = 160
)

var (
	editHunkRe    = regexp.MustCompile(`^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@`)
	diffRemovedRe = regexp.MustCompile(`^\d+ - `)
	diffAddedRe   = regexp.MustCompile(`^\d+ \+ `)
)

func formatToolActivityLine(entry *toolEntry) string {
	head := "● " + entry.toolName
	if entry.preview != "" {
		head += "  " + entry.preview
	}
	switch {
	case entry.outcome != "" && entry.duration != "" && entry.message == "":
		return fmt.Sprintf("%s  %s %s\n", head, entry.outcome, entry.duration)
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

func renderToolActivityLine(entry *toolEntry) string {
	markerColor := defaultTheme.accent
	if entry.outcome == "ok" {
		if entry.groupKind == "exploration" {
			markerColor = defaultTheme.textMuted
		} else {
			markerColor = defaultTheme.successSoft
		}
	} else if entry.outcome == "error" {
		markerColor = defaultTheme.err
	} else if entry.groupKind == "exploration" {
		markerColor = defaultTheme.textMuted
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

func toolOutcomeColor(outcome string) lipgloss.TerminalColor {
	switch outcome {
	case "ok":
		return defaultTheme.successSoft
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
	if *activity.DurationMS < 1000 {
		return fmt.Sprintf("%dms", *activity.DurationMS)
	}
	return fmt.Sprintf("%.1fs", float64(*activity.DurationMS)/1000.0)
}

func buildToolDetailLines(activity *rpc.ToolActivity) []string {
	if activity == nil || activity.Details == nil {
		return nil
	}
	kind, _ := activity.Details["kind"].(string)
	if kind != "edit" {
		return nil
	}
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

func isOperationalMiss(result any) bool {
	m, ok := result.(map[string]any)
	if !ok {
		return false
	}
	flag, ok := m["ok"].(bool)
	return ok && !flag
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
