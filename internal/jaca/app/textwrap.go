package app

import (
	"regexp"
	"strings"
)

// ansiEscapeRe matches ANSI escape sequences so they can be skipped when measuring visible line width.
var ansiEscapeRe = regexp.MustCompile(`\x1b\[[0-9;]*m`)
var osc8EscapeRe = regexp.MustCompile(`\x1b]8;;[^\x1b]*\x1b\\`)

func stripNonPrintingEscapes(text string) string {
	text = ansiEscapeRe.ReplaceAllString(text, "")
	return osc8EscapeRe.ReplaceAllString(text, "")
}

func visibleLen(text string) int {
	return len([]rune(stripNonPrintingEscapes(text)))
}

func wrapLines(text string, width int) string {
	if width <= 0 {
		return text
	}
	lines := strings.Split(text, "\n")
	var out strings.Builder
	out.Grow(len(text) + len(text)/4)
	for i, line := range lines {
		if i > 0 {
			out.WriteByte('\n')
		}
		if visibleLen(line) <= width {
			out.WriteString(line)
			continue
		}
		wrapSingleLine(&out, line, width)
	}
	return out.String()
}

func leadingSpaces(line string) string {
	stripped := stripNonPrintingEscapes(line)
	trimmed := strings.TrimLeft(stripped, " ")
	n := len(stripped) - len(trimmed)
	if n == 0 {
		return ""
	}
	return strings.Repeat(" ", n)
}

type visibleToken struct {
	text    string
	visible int
}

func wrapSingleLine(out *strings.Builder, line string, width int) {
	indent := leadingSpaces(line)
	indentWidth := len([]rune(indent))
	var tokens []visibleToken
	cursor := 0
	escapeLocs := append([][]int{}, ansiEscapeRe.FindAllStringIndex(line, -1)...)
	escapeLocs = append(escapeLocs, osc8EscapeRe.FindAllStringIndex(line, -1)...)
	escapeLocs = sortEscapeLocs(escapeLocs)
	for _, loc := range escapeLocs {
		if loc[0] > cursor {
			tokens = append(tokens, splitVisibleTokens(line[cursor:loc[0]])...)
		}
		tokens = append(tokens, visibleToken{text: line[loc[0]:loc[1]], visible: 0})
		cursor = loc[1]
	}
	if cursor < len(line) {
		tokens = append(tokens, splitVisibleTokens(line[cursor:])...)
	}
	col := 0
	firstLine := true
	for _, tok := range tokens {
		if tok.visible == 0 {
			out.WriteString(tok.text)
			continue
		}
		if col > 0 && col+tok.visible > width {
			out.WriteByte('\n')
			out.WriteString(indent)
			col = indentWidth
			firstLine = false
			trimmed := strings.TrimLeft(tok.text, " ")
			trimmedVisible := len([]rune(trimmed))
			if trimmedVisible == 0 {
				continue
			}
			out.WriteString(trimmed)
			col += trimmedVisible
			continue
		}
		if !firstLine && col == indentWidth && strings.TrimSpace(tok.text) == "" {
			continue
		}
		out.WriteString(tok.text)
		col += tok.visible
	}
}

func sortEscapeLocs(locs [][]int) [][]int {
	if len(locs) < 2 {
		return locs
	}
	for i := 0; i < len(locs)-1; i++ {
		for j := i + 1; j < len(locs); j++ {
			if locs[j][0] < locs[i][0] {
				locs[i], locs[j] = locs[j], locs[i]
			}
		}
	}
	return locs
}

func splitVisibleTokens(text string) []visibleToken {
	var tokens []visibleToken
	runes := []rune(text)
	i := 0
	for i < len(runes) {
		if runes[i] == ' ' {
			j := i
			for j < len(runes) && runes[j] == ' ' {
				j++
			}
			tokens = append(tokens, visibleToken{text: string(runes[i:j]), visible: j - i})
			i = j
		} else {
			j := i
			for j < len(runes) && runes[j] != ' ' {
				j++
			}
			tokens = append(tokens, visibleToken{text: string(runes[i:j]), visible: j - i})
			i = j
		}
	}
	return tokens
}

func truncateInline(text string, limit int) string {
	normalized := strings.Join(strings.Fields(text), " ")
	if len(normalized) <= limit {
		return normalized
	}
	return strings.TrimSpace(normalized[:limit-3]) + "..."
}

func truncateDisplayLine(text string, limit int) string {
	if limit <= 0 {
		return ""
	}
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	if limit <= 3 {
		return string(runes[:limit])
	}
	return string(runes[:limit-3]) + "..."
}
