package app

import (
	"fmt"
	"os"
	"regexp"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"jaca/internal/jaca/rpc"
)

type transcriptBlock struct {
	plain    string
	rendered string
	kind     transcriptBlockKind
}

type transcriptBlockKind uint8

const (
	transcriptBlockRaw transcriptBlockKind = iota
	transcriptBlockAssistantMarkdown
)

var brailleSpinner = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

type Transcript struct {
	blocks           []transcriptBlock
	liveAssistantIdx int
	toolGroup        *toolGroup
	renderedCache    string
	renderOffsets    []int
	dirtyFrom        int
	Width            int
	MotionTick       int
}

type toolEntry struct {
	toolName        string
	preview         string
	outcome         string
	message         string
	duration        string
	detailLines     []string
	resultLines     []string
	resultTruncated bool
	operationalMiss bool
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

func NewTranscript() *Transcript {
	return &Transcript{
		liveAssistantIdx: -1,
		dirtyFrom:        -1,
	}
}

func (t *Transcript) Render() string {
	if t.dirtyFrom == -1 {
		return t.renderedCache
	}

	startIndex := t.dirtyFrom
	prefix := ""
	offsets := make([]int, len(t.blocks)+1)
	if startIndex > 0 && len(t.renderOffsets) > startIndex {
		prefix = t.renderedCache[:t.renderOffsets[startIndex]]
		copy(offsets[:startIndex+1], t.renderOffsets[:startIndex+1])
	} else {
		startIndex = 0
	}

	var rendered strings.Builder
	rendered.Grow(len(prefix))
	rendered.WriteString(prefix)
	currentOffset := len(prefix)
	for i := startIndex; i < len(t.blocks); i++ {
		offsets[i] = currentOffset
		blockRendered := t.blocks[i].rendered
		if blockRendered == "" {
			blockRendered = renderTranscriptBlock(t.blocks[i])
			t.blocks[i].rendered = blockRendered
		}
		rendered.WriteString(blockRendered)
		currentOffset += len(blockRendered)
	}
	offsets[len(t.blocks)] = currentOffset

	t.renderedCache = rendered.String()
	t.renderOffsets = offsets
	t.dirtyFrom = -1
	t.discardImmutableRenderedBlocks()
	return t.renderedCache
}

func (t *Transcript) discardImmutableRenderedBlocks() {
	mutable := map[int]struct{}{}
	if t.liveAssistantIdx >= 0 {
		mutable[t.liveAssistantIdx] = struct{}{}
	}
	if t.toolGroup != nil {
		mutable[t.toolGroup.index] = struct{}{}
	}
	for i := range t.blocks {
		if _, ok := mutable[i]; ok {
			continue
		}
		if t.blocks[i].kind == transcriptBlockAssistantMarkdown {
			t.blocks[i].rendered = ""
		}
	}
}

func (t *Transcript) appendBlock(block transcriptBlock) int {
	t.blocks = append(t.blocks, block)
	index := len(t.blocks) - 1
	t.markDirty(index)
	return index
}

func (t *Transcript) replaceBlock(index int, block transcriptBlock) {
	t.blocks[index] = block
	t.markDirty(index)
}

func (t *Transcript) markDirty(index int) {
	if index < 0 {
		return
	}
	if t.dirtyFrom == -1 || index < t.dirtyFrom {
		t.dirtyFrom = index
	}
}

func (t *Transcript) WriteStartupBanner(model string, workspaceRoot string, thinking string) {
	titleStyle := lipgloss.NewStyle().Foreground(defaultTheme.text).Bold(true)
	labelStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	valueStyle := lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
	hintStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)

	var innerLines []string
	var innerRendered []string

	innerLines = append(innerLines, ">_ jaca (v0.1.0)", "")
	innerRendered = append(innerRendered, titleStyle.Render(">_ jaca (v0.1.0)"), "")

	innerLines = append(innerLines,
		fmt.Sprintf("model:     %s    /model to change", model),
		fmt.Sprintf("directory: %s", displayPath(workspaceRoot)),
	)
	innerRendered = append(innerRendered,
		labelStyle.Render("model:     ")+valueStyle.Render(model)+"    "+hintStyle.Render("/model to change"),
		labelStyle.Render("directory: ")+valueStyle.Render(displayPath(workspaceRoot)),
	)
	if thinking != "" {
		innerLines = append(innerLines, fmt.Sprintf("thinking:  %s", thinking))
		innerRendered = append(innerRendered,
			labelStyle.Render("thinking:  ")+valueStyle.Render(thinking),
		)
	}

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(defaultTheme.border).
		Padding(0, 1)

	plainBox := boxStyle.Render(strings.Join(innerLines, "\n"))
	renderedBox := boxStyle.Render(strings.Join(innerRendered, "\n"))

	var extraPlain, extraRendered string
	if strings.HasPrefix(model, "openai") && os.Getenv("OPENAI_API_KEY") == "" {
		extraPlain = "\nno OPENAI_API_KEY\nuse /provider openai"
		extraRendered = "\n" +
			lipgloss.NewStyle().Foreground(defaultTheme.err).Render("no OPENAI_API_KEY") + "\n" +
			hintStyle.Render("use /provider openai")
	} else if strings.HasPrefix(model, "anthropic") && os.Getenv("ANTHROPIC_API_KEY") == "" {
		extraPlain = "\nno ANTHROPIC_API_KEY\nuse /provider anthropic"
		extraRendered = "\n" +
			lipgloss.NewStyle().Foreground(defaultTheme.err).Render("no ANTHROPIC_API_KEY") + "\n" +
			hintStyle.Render("use /provider anthropic")
	}

	t.appendBlock(transcriptBlock{
		plain:    plainBox + extraPlain + "\n\n",
		rendered: renderedBox + extraRendered + "\n\n",
	})
}

func (t *Transcript) WriteHelp() {
	t.WriteNote("commands", []string{
		"  /help              show this help",
		"  /provider          switch active provider",
		"  /auth <provider>   save provider credentials",
		"  /model <name>      switch model",
		"  /trace <mode>      set tracing mode",
		"  /thinking <level>  set thinking level",
		"  /workspace         show workspace root",
		"  /session           show session info",
		"  /compact           compact current session",
		"  /new               start a new session",
		"  /quit              exit",
		"",
		"keyboard",
		"  up                 previous prompt",
		"  down               next prompt / restore draft",
		"  ctrl+u             clear prompt",
		"  esc                interrupt active run, esc again edits previous prompt",
		"  ctrl+c             copy-safe, ctrl+c again quits when idle",
		"",
		"provider setup",
		"  /provider ollama                     local ollama, no key needed",
		"  /provider openai                     select OpenAI, auth starts if needed",
		"  /provider anthropic                  select Anthropic, auth starts if needed",
		"  /auth openai                         save OPENAI_API_KEY",
		"  /auth anthropic                      save ANTHROPIC_API_KEY",
		"",
		"tracing",
		"  /trace off                           disable tracing",
		"  /trace local                         store traces locally",
		"  /trace logfire                       export traces to Logfire",
	})
}

func (t *Transcript) WriteNote(title string, lines []string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	header := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("note") +
		"  " + lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Bold(true).Render(title)
	plain := "note  " + title + "\n"
	rendered := header + "\n"
	for _, line := range lines {
		plain += line + "\n"
		rendered += line + "\n"
	}
	if len(lines) > 0 {
		rendered += "\n"
		plain += "\n"
	}
	t.appendBlock(transcriptBlock{plain: plain, rendered: rendered})
}

func (t *Transcript) WriteUserTurn(prompt string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	plainLine := "> " + prompt
	width := t.Width
	if width <= 0 {
		width = 80
	}
	rendered := lipgloss.NewStyle().
		Foreground(defaultTheme.text).
		Bold(true).
		Background(defaultTheme.border).
		Width(width).
		Render(plainLine)
	t.appendBlock(transcriptBlock{
		plain:    plainLine + "\n",
		rendered: rendered + "\n",
	})
}

func (t *Transcript) WriteLine(line string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.appendBlock(transcriptBlock{plain: line + "\n", rendered: line + "\n"})
}

func (t *Transcript) WriteError(message string) {
	t.WriteLine("ERROR: " + message)
}

func (t *Transcript) ApplyRunEvent(event rpc.RunEvent) {
	switch event.Type {
	case "assistant_text_delta":
		t.appendAssistantDelta(event.Delta)
	case "tool_call_started":
		t.startTool(event)
	case "tool_call_updated":
		t.updateTool(event)
	case "tool_call_succeeded":
		t.finishTool(event)
	case "tool_call_failed":
		t.failTool(event)
	case "run_failed":
		t.endLiveAssistant()
		t.appendBlock(transcriptBlock{
			plain:    "error  " + event.Message + "\n",
			rendered: "error  " + event.Message + "\n",
		})
	case "run_succeeded":
		t.completeAssistant(event.OutputText)
	}
}

func (t *Transcript) appendAssistantDelta(delta string) {
	t.endToolGroup()
	if t.liveAssistantIdx == -1 {
		t.ensureBlockGap()
		t.liveAssistantIdx = t.appendBlock(transcriptBlock{
			plain: delta,
		})
		t.rebuildLiveAssistantRendered()
		return
	}
	block := &t.blocks[t.liveAssistantIdx]
	block.plain += delta
	t.rebuildLiveAssistantRendered()
}

func (t *Transcript) completeAssistant(markdown string) {
	t.endToolGroup()
	rendered := renderCompletedAssistantMarkdown(markdown)
	if t.liveAssistantIdx != -1 {
		t.replaceBlock(t.liveAssistantIdx, transcriptBlock{
			plain:    markdown + "\n",
			rendered: rendered + "\n",
			kind:     transcriptBlockAssistantMarkdown,
		})
		t.liveAssistantIdx = -1
		return
	}
	t.appendBlock(transcriptBlock{
		plain:    markdown + "\n",
		rendered: rendered + "\n",
		kind:     transcriptBlockAssistantMarkdown,
	})
}

func (t *Transcript) endLiveAssistant() {
	if t.liveAssistantIdx >= 0 {
		block := &t.blocks[t.liveAssistantIdx]
		markdown := strings.TrimRight(block.plain, "\n")
		rendered := renderCompletedAssistantMarkdown(markdown)
		t.replaceBlock(t.liveAssistantIdx, transcriptBlock{
			plain:    markdown + "\n",
			rendered: rendered + "\n",
			kind:     transcriptBlockAssistantMarkdown,
		})
	}
	t.liveAssistantIdx = -1
}

// Smooth breathing cycle for the live ● marker.
// Ramps from dim to full and back over 12 steps (~1.7s at 140ms tick).
var livePulseGradient = []lipgloss.TerminalColor{
	themeColor("#3d3520", "238", "8"),  // near-invisible
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

func (t *Transcript) rebuildLiveAssistantRendered() {
	if t.liveAssistantIdx < 0 {
		return
	}
	block := &t.blocks[t.liveAssistantIdx]
	idx := t.MotionTick % len(livePulseGradient)
	markerColor := livePulseGradient[idx]
	block.rendered = lipgloss.NewStyle().Foreground(markerColor).Render("● ") +
		lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(block.plain)
	t.markDirty(t.liveAssistantIdx)
}

func (t *Transcript) RefreshLiveMarker() {
	if t.liveAssistantIdx < 0 {
		return
	}
	t.rebuildLiveAssistantRendered()
}

func (t *Transcript) ensureBlockGap() {
	if len(t.blocks) == 0 {
		return
	}
	last := t.blocks[len(t.blocks)-1].plain
	if strings.HasSuffix(last, "\n\n") {
		return
	}
	if strings.HasSuffix(last, "\n") {
		t.appendBlock(transcriptBlock{plain: "\n", rendered: "\n"})
		return
	}
	t.appendBlock(transcriptBlock{plain: "\n\n", rendered: "\n\n"})
}

func (t *Transcript) startTool(event rpc.RunEvent) {
	t.endLiveAssistant()
	if t.toolGroup == nil {
		index := t.appendBlock(transcriptBlock{})
		t.toolGroup = &toolGroup{
			index:   index,
			entries: map[string]*toolEntry{},
		}
	}
	t.toolGroup.order = append(t.toolGroup.order, event.ToolCallID)
	t.toolGroup.entries[event.ToolCallID] = &toolEntry{
		toolName: event.ToolName,
		preview:  buildToolPreview(event.ToolName, event.Args, event.ArgsValid, event.Activity),
	}
	t.rewriteToolGroup()
}

func (t *Transcript) finishTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	entry := t.toolGroup.entries[event.ToolCallID]
	if entry == nil {
		return
	}
	miss := isOperationalMiss(event.Result)
	entry.operationalMiss = miss
	if miss {
		entry.outcome = ""
	} else {
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
		entry.resultLines, entry.resultTruncated = extractToolResultLines(event.Result)
	}
	if len(entry.detailLines) == 0 && len(entry.resultLines) == 0 && entry.message == "" {
		entry.message = buildToolSummary(event.Activity, "")
	}
	t.rewriteToolGroup()
}

func (t *Transcript) updateTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	entry := t.toolGroup.entries[event.ToolCallID]
	if entry == nil {
		return
	}
	entry.outcome = "running"
	entry.message = buildToolSummary(event.Activity, "")
	entry.duration = buildToolDuration(event.Activity)
	entry.detailLines = nil
	entry.resultLines, entry.resultTruncated = extractToolResultLines(event.Partial)
	t.rewriteToolGroup()
}

func (t *Transcript) failTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	entry := t.toolGroup.entries[event.ToolCallID]
	if entry == nil {
		return
	}
	entry.outcome = "error"
	entry.message = buildToolSummary(event.Activity, event.Message)
	entry.duration = buildToolDuration(event.Activity)
	entry.detailLines = nil
	entry.resultLines = nil
	entry.resultTruncated = false
	t.rewriteToolGroup()
}

func (t *Transcript) endToolGroup() {
	t.toolGroup = nil
}

func (t *Transcript) rewriteToolGroup() {
	if t.toolGroup == nil {
		return
	}
	var plain strings.Builder
	var rendered strings.Builder
	for _, toolCallID := range t.toolGroup.order {
		entry := t.toolGroup.entries[toolCallID]
		plain.WriteString(formatToolActivityLine(entry))
		rendered.WriteString(renderToolActivityLine(entry))
		for _, line := range entry.detailLines {
			plain.WriteString(line + "\n")
			rendered.WriteString(styleToolDetailLine(line) + "\n")
		}
		resultColor := defaultTheme.textMuted
		if entry.operationalMiss {
			resultColor = defaultTheme.errSoft
		}
		for idx, line := range entry.resultLines {
			prefix := "    "
			if idx == 0 {
				prefix = "  └ "
			}
			plain.WriteString(prefix + line + "\n")
			rendered.WriteString(lipgloss.NewStyle().Foreground(resultColor).Render(prefix+line) + "\n")
		}
		if entry.resultTruncated {
			plain.WriteString("    ...\n")
			rendered.WriteString(lipgloss.NewStyle().Foreground(resultColor).Render("    ...") + "\n")
		}
	}
	t.replaceBlock(t.toolGroup.index, transcriptBlock{
		plain:    plain.String(),
		rendered: rendered.String(),
	})
}

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
		markerColor = defaultTheme.successSoft
	} else if entry.outcome == "error" {
		markerColor = defaultTheme.err
	}
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Foreground(markerColor).Render("● "))
	b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(entry.toolName))
	if entry.preview != "" {
		b.WriteString("  ")
		b.WriteString(lipgloss.NewStyle().Foreground(defaultTheme.text).Render(entry.preview))
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

var diffRemovedRe = regexp.MustCompile(`^\d+ - `)
var diffAddedRe = regexp.MustCompile(`^\d+ \+ `)

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

func isOperationalMiss(result any) bool {
	m, ok := result.(map[string]any)
	if !ok {
		return false
	}
	flag, ok := m["ok"].(bool)
	return ok && !flag
}

func extractToolResultLines(result any) ([]string, bool) {
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
	return nil, false
}

func truncateLines(lines []string, limit int) ([]string, bool) {
	filtered := make([]string, 0, len(lines))
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		filtered = append(filtered, truncateDisplayLine(line, maxToolResultLineChars))
	}
	if len(filtered) > limit {
		return filtered[:limit], true
	}
	return filtered, false
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
		lines = append(lines, "  â”‚ "+strings.Join(summary, ", "))
	}
	diff, _ := activity.Details["diff"].(string)
	if diff != "" {
		lines = append(lines, renderEditDiffLines(diff)...)
	}
	return lines
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

var (
	editHunkRe                = regexp.MustCompile(`^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@`)
	assistantHeadingRe        = regexp.MustCompile(`^(#{1,6})\s+(.*)$`)
	assistantUnorderedItemRe  = regexp.MustCompile(`^[-*+]\s+(.*)$`)
	assistantOrderedItemRe    = regexp.MustCompile(`^(\d+)\.\s+(.*)$`)
	assistantInlineTokenRe    = regexp.MustCompile("(`[^`]+`|\\*\\*[^*]+\\*\\*)")
	assistantParagraphStyle   = lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
	assistantCodeStyle        = lipgloss.NewStyle().Foreground(defaultTheme.accentSoft)
	assistantCodeBlockStyle   = lipgloss.NewStyle().Foreground(defaultTheme.text)
	assistantMutedPrefixStyle = lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
)

func renderCompletedAssistantMarkdown(markdown string) string {
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

		b.WriteString(prefix)
		b.WriteString(renderAssistantInline(line, assistantParagraphStyle))
		b.WriteByte('\n')
	}

	if b.Len() == 0 {
		return ""
	}
	return b.String()
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
			lines = append(lines, "  â”‚ "+raw)
			continue
		}
		switch {
		case strings.HasPrefix(raw, " "):
			lines = append(lines, fmt.Sprintf("  â”‚ %d   %s", newLine, raw[1:]))
			oldLine++
			newLine++
		case strings.HasPrefix(raw, "-"):
			lines = append(lines, fmt.Sprintf("  â”‚ %d - %s", oldLine, raw[1:]))
			oldLine++
		case strings.HasPrefix(raw, "+"):
			lines = append(lines, fmt.Sprintf("  â”‚ %d + %s", newLine, raw[1:]))
			newLine++
		}
	}
	return lines
}

func atoiSafe(raw string) int {
	var n int
	fmt.Sscanf(raw, "%d", &n)
	return n
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

func renderTranscriptBlock(block transcriptBlock) string {
	switch block.kind {
	case transcriptBlockAssistantMarkdown:
		return renderCompletedAssistantMarkdown(strings.TrimSuffix(block.plain, "\n"))
	default:
		return block.plain
	}
}
