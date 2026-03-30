package app

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Smooth breathing cycle for the live ● marker.
// Ramps from dim to full and back over 12 steps (~1.7s at 140ms tick).
var livePulseGradient = []lipgloss.TerminalColor{
	themeColor("#3d3520", "238", "8"), // near-invisible
	themeColor("#5a4e2e", "240", "8"),
	themeColor("#77673c", "242", "11"),
	themeColor("#94804a", "244", "11"),
	themeColor("#b19958", "179", "11"),
	themeColor("#d79a41", "179", "11"), // full accent
	themeColor("#d79a41", "179", "11"), // hold at peak
	themeColor("#b19958", "179", "11"),
	themeColor("#94804a", "244", "11"),
	themeColor("#77673c", "242", "11"),
	themeColor("#5a4e2e", "240", "8"),
	themeColor("#3d3520", "238", "8"),
}

var (
	assistantHeadingRe        = regexp.MustCompile(`^(#{1,6})\s+(.*)$`)
	assistantUnorderedItemRe  = regexp.MustCompile(`^[-*+]\s+(.*)$`)
	assistantOrderedItemRe    = regexp.MustCompile(`^(\d+)\.\s+(.*)$`)
	assistantInlineTokenRe    = regexp.MustCompile("(`[^`]+`|\\*\\*[^*]+\\*\\*|~~[^~]+~~)")
	assistantBlockquoteRe     = regexp.MustCompile(`^(>{1,})\s?(.*)$`)
	assistantHorizontalRuleRe = regexp.MustCompile(`^(\s*[-*_]\s*){3,}$`)
	assistantCodeFenceLangRe  = regexp.MustCompile("^```(\\w+)")
	assistantParagraphStyle   = lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
	assistantCodeStyle        = lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
	assistantCodeBlockStyle   = lipgloss.NewStyle().Foreground(defaultTheme.text)
	assistantMutedPrefixStyle = lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	assistantStrikeStyle      = lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Strikethrough(true)
	assistantBlockquoteBar    = lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	assistantBlockquoteText   = lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
)

func renderCompletedAssistantMarkdown(markdown string) string {
	if strings.TrimSpace(markdown) == "" {
		marker := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("● ")
		empty := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("[empty response]")
		return marker + empty + "\n"
	}

	var b strings.Builder
	marker := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("● ")
	firstContent := true
	inCodeBlock := false

	for _, rawLine := range strings.Split(markdown, "\n") {
		line := strings.TrimRight(rawLine, " \t\r")
		if strings.HasPrefix(line, "```") {
			inCodeBlock = !inCodeBlock
			if b.Len() > 0 && !strings.HasSuffix(b.String(), "\n\n") {
				b.WriteByte('\n')
			}
			if inCodeBlock {
				if m := assistantCodeFenceLangRe.FindStringSubmatch(line); m != nil {
					b.WriteString(assistantMutedPrefixStyle.Render("    " + m[1]))
					b.WriteByte('\n')
				}
			}
			continue
		}

		if inCodeBlock {
			b.WriteString(assistantMutedPrefixStyle.Render("    "))
			b.WriteString(assistantCodeBlockStyle.Render(line))
			b.WriteByte('\n')
			continue
		}

		if line == "" {
			if !firstContent {
				b.WriteByte('\n')
			}
			continue
		}

		prefix := ""
		if firstContent {
			prefix = marker
			firstContent = false
		}

		if match := assistantHeadingRe.FindStringSubmatch(line); match != nil {
			if prefix == "" && b.Len() > 0 && !strings.HasSuffix(b.String(), "\n\n") {
				b.WriteByte('\n')
			}
			b.WriteString(prefix)
			b.WriteString(assistantParagraphStyle.Bold(true).Render(match[2]))
			b.WriteByte('\n')
			continue
		}

		if match := assistantUnorderedItemRe.FindStringSubmatch(line); match != nil {
			b.WriteString(prefix)
			b.WriteString(assistantMutedPrefixStyle.Render("    "))
			b.WriteString(renderAssistantInline(match[1], assistantParagraphStyle))
			b.WriteByte('\n')
			continue
		}

		if match := assistantOrderedItemRe.FindStringSubmatch(line); match != nil {
			b.WriteString(prefix)
			b.WriteString(assistantMutedPrefixStyle.Render(fmt.Sprintf("  %s. ", match[1])))
			b.WriteString(renderAssistantInline(match[2], assistantParagraphStyle))
			b.WriteByte('\n')
			continue
		}

		if assistantHorizontalRuleRe.MatchString(line) {
			b.WriteString(prefix)
			b.WriteString(assistantMutedPrefixStyle.Render(strings.Repeat("─", 40)))
			b.WriteByte('\n')
			continue
		}

		if match := assistantBlockquoteRe.FindStringSubmatch(line); match != nil {
			depth := len(match[1])
			bar := strings.Repeat(assistantBlockquoteBar.Render("│ "), depth)
			b.WriteString(prefix)
			b.WriteString(bar)
			b.WriteString(renderAssistantInline(match[2], assistantBlockquoteText))
			b.WriteByte('\n')
			continue
		}

		b.WriteString(prefix)
		b.WriteString(renderAssistantInline(line, assistantParagraphStyle))
		b.WriteByte('\n')
	}

	if b.Len() == 0 {
		return ""
	}
	return b.String()
}

// StreamCollector processes markdown incrementally during streaming,
// committing only completed lines (up to last \n) through the markdown renderer.
type StreamCollector struct {
	buf            strings.Builder
	committedLines int    // number of rendered output lines already emitted
	rendered       string // accumulated rendered output so far
}

// PushDelta appends new streaming text to the internal buffer.
func (sc *StreamCollector) PushDelta(delta string) {
	sc.buf.WriteString(delta)
}

// CommitCompleteLines renders all complete lines (up to the last \n) through
// renderCompletedAssistantMarkdown and returns the accumulated rendered output.
func (sc *StreamCollector) CommitCompleteLines() string {
	text := sc.buf.String()
	lastNL := strings.LastIndex(text, "\n")
	if lastNL < 0 {
		return sc.rendered
	}
	committed := text[:lastNL+1]
	rendered := renderCompletedAssistantMarkdown(strings.TrimSuffix(committed, "\n"))
	newLines := strings.Count(rendered, "\n")
	if newLines > sc.committedLines {
		sc.rendered = rendered
		sc.committedLines = newLines
	}
	return sc.rendered
}

// FinalizeAndDrain renders the full buffer (including any partial trailing line)
// through the markdown renderer, resets state, and returns the result.
func (sc *StreamCollector) FinalizeAndDrain() string {
	text := strings.TrimSuffix(sc.buf.String(), "\n")
	result := renderCompletedAssistantMarkdown(text)
	sc.Reset()
	return result
}

// Reset clears the collector state.
func (sc *StreamCollector) Reset() {
	sc.buf.Reset()
	sc.committedLines = 0
	sc.rendered = ""
}

// PlainText returns the full accumulated buffer contents.
func (sc *StreamCollector) PlainText() string {
	return sc.buf.String()
}

// PartialTail returns the uncommitted text after the last newline.
func (sc *StreamCollector) PartialTail() string {
	text := sc.buf.String()
	lastNL := strings.LastIndex(text, "\n")
	if lastNL < 0 {
		return text
	}
	return text[lastNL+1:]
}

func renderAssistantInline(content string, baseStyle lipgloss.Style) string {
	var b strings.Builder
	cursor := 0
	for _, match := range assistantInlineTokenRe.FindAllStringIndex(content, -1) {
		if match[0] > cursor {
			b.WriteString(baseStyle.Render(content[cursor:match[0]]))
		}
		token := content[match[0]:match[1]]
		switch {
		case strings.HasPrefix(token, "`") && len(token) >= 2:
			b.WriteString(assistantCodeStyle.Render(token[1 : len(token)-1]))
		case strings.HasPrefix(token, "**") && strings.HasSuffix(token, "**") && len(token) >= 4:
			b.WriteString(baseStyle.Bold(true).Render(token[2 : len(token)-2]))
		case strings.HasPrefix(token, "~~") && strings.HasSuffix(token, "~~") && len(token) >= 4:
			b.WriteString(assistantStrikeStyle.Render(token[2 : len(token)-2]))
		default:
			b.WriteString(baseStyle.Render(token))
		}
		cursor = match[1]
	}
	if cursor < len(content) {
		b.WriteString(baseStyle.Render(content[cursor:]))
	}
	return b.String()
}
