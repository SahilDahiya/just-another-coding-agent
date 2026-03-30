package app

import (
	"fmt"
	"os"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"jaca/internal/jaca/rpc"
)

// Cell is the interface for all transcript block types.
type Cell interface {
	Plain() string
	Render() string
	IsMarkdown() bool
}

// rawCell holds pre-computed plain and rendered text (banners, notes, user turns, errors, gaps).
type rawCell struct {
	plain    string
	rendered string
}

func (c *rawCell) Plain() string    { return c.plain }
func (c *rawCell) Render() string   { return c.rendered }
func (c *rawCell) IsMarkdown() bool { return false }

// assistantCell holds completed assistant markdown with lazy, evictable rendering.
type assistantCell struct {
	plain       string
	cachedRender string
}

func (c *assistantCell) Plain() string { return c.plain }
func (c *assistantCell) Render() string {
	if c.cachedRender != "" {
		return c.cachedRender
	}
	c.cachedRender = renderCompletedAssistantMarkdown(strings.TrimSuffix(c.plain, "\n")) + "\n"
	return c.cachedRender
}
func (c *assistantCell) IsMarkdown() bool { return true }

// toolGroupCell holds pre-computed tool group text, rebuilt by rewriteToolGroup.
type toolGroupCell struct {
	plain    string
	rendered string
}

func (c *toolGroupCell) Plain() string    { return c.plain }
func (c *toolGroupCell) Render() string   { return c.rendered }
func (c *toolGroupCell) IsMarkdown() bool { return false }

type Transcript struct {
	blocks           []Cell
	liveAssistantIdx int
	liveDeltaBuf     strings.Builder
	streamCollector  *StreamCollector
	toolGroup        *toolGroup
	renderedCache    string
	renderOffsets    []int
	dirtyFrom        int
	Width            int
	MotionTick       int
}

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
		blockRendered := t.blocks[i].Render()
		if t.Width > 0 {
			blockRendered = wrapLines(blockRendered, t.Width)
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
	for i := range t.blocks {
		if i == t.liveAssistantIdx {
			continue
		}
		if t.toolGroup != nil && i == t.toolGroup.index {
			continue
		}
		if ac, ok := t.blocks[i].(*assistantCell); ok {
			ac.cachedRender = ""
		}
	}
}

func (t *Transcript) appendBlock(block Cell) int {
	t.blocks = append(t.blocks, block)
	index := len(t.blocks) - 1
	t.markDirty(index)
	return index
}

func (t *Transcript) replaceBlock(index int, block Cell) {
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

	t.appendBlock(&rawCell{
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
	t.appendBlock(&rawCell{plain: plain, rendered: rendered})
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
	t.appendBlock(&rawCell{
		plain:    plainLine + "\n",
		rendered: rendered + "\n",
	})
}

func (t *Transcript) WriteLine(line string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.appendBlock(&rawCell{plain: line + "\n", rendered: line + "\n"})
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
		t.appendBlock(&rawCell{
			plain:    "error  " + event.Message + "\n",
			rendered: "error  " + event.Message + "\n",
		})
	case "run_succeeded":
		t.completeAssistant(event.OutputText)
	}
}

func (t *Transcript) appendAssistantDelta(delta string) {
	t.endToolGroup()
	if t.streamCollector == nil {
		t.streamCollector = &StreamCollector{}
	}
	if t.liveAssistantIdx == -1 {
		t.ensureBlockGap()
		t.liveDeltaBuf.Reset()
		t.streamCollector.Reset()
		t.liveDeltaBuf.WriteString(delta)
		t.streamCollector.PushDelta(delta)
		t.liveAssistantIdx = t.appendBlock(&rawCell{
			plain: delta,
		})
		t.rebuildLiveAssistantRendered()
		return
	}
	t.liveDeltaBuf.WriteString(delta)
	t.streamCollector.PushDelta(delta)
	t.blocks[t.liveAssistantIdx].(*rawCell).plain = t.liveDeltaBuf.String()
	t.rebuildLiveAssistantRendered()
}

func (t *Transcript) completeAssistant(markdown string) {
	t.endToolGroup()
	rendered := renderCompletedAssistantMarkdown(markdown)
	cell := &assistantCell{
		plain:        markdown + "\n",
		cachedRender: rendered + "\n",
	}
	if t.streamCollector != nil {
		t.streamCollector.Reset()
	}
	if t.liveAssistantIdx != -1 {
		t.replaceBlock(t.liveAssistantIdx, cell)
		t.liveAssistantIdx = -1
		return
	}
	t.appendBlock(cell)
}

func (t *Transcript) endLiveAssistant() {
	if t.liveAssistantIdx >= 0 {
		markdown := strings.TrimRight(t.blocks[t.liveAssistantIdx].Plain(), "\n")
		rendered := renderCompletedAssistantMarkdown(markdown)
		t.replaceBlock(t.liveAssistantIdx, &assistantCell{
			plain:        markdown + "\n",
			cachedRender: rendered + "\n",
		})
	}
	t.liveAssistantIdx = -1
	t.liveDeltaBuf.Reset()
	if t.streamCollector != nil {
		t.streamCollector.Reset()
	}
}

func (t *Transcript) rebuildLiveAssistantRendered() {
	if t.liveAssistantIdx < 0 {
		return
	}
	rc := t.blocks[t.liveAssistantIdx].(*rawCell)
	idx := t.MotionTick % len(livePulseGradient)
	markerColor := livePulseGradient[idx]
	marker := lipgloss.NewStyle().Foreground(markerColor).Render("● ")

	if t.streamCollector != nil {
		committed := t.streamCollector.CommitCompleteLines()
		partial := t.streamCollector.PartialTail()
		if committed != "" {
			// Strip the leading "● " from committed markdown since we prepend our own animated marker
			committedNoMarker := stripLeadingRenderedMarker(committed)
			rc.rendered = marker + committedNoMarker +
				lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(partial)
		} else {
			rc.rendered = marker +
				lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(partial)
		}
	} else {
		rc.rendered = marker +
			lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(rc.plain)
	}
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
	last := t.blocks[len(t.blocks)-1].Plain()
	if strings.HasSuffix(last, "\n\n") {
		return
	}
	if strings.HasSuffix(last, "\n") {
		t.appendBlock(&rawCell{plain: "\n", rendered: "\n"})
		return
	}
	t.appendBlock(&rawCell{plain: "\n\n", rendered: "\n\n"})
}

func (t *Transcript) startTool(event rpc.RunEvent) {
	t.endLiveAssistant()
	if t.toolGroup == nil {
		t.ensureBlockGap()
		index := t.appendBlock(&toolGroupCell{})
		t.toolGroup = &toolGroup{
			index:   index,
			entries: map[string]*toolEntry{},
		}
	}
	t.toolGroup.order = append(t.toolGroup.order, event.ToolCallID)
	t.toolGroup.entries[event.ToolCallID] = &toolEntry{
		toolName:  event.ToolName,
		preview:   buildToolPreview(event.ToolName, event.Args, event.ArgsValid, event.Activity),
		groupKind: buildToolGroupKind(event.Activity),
		activity:  event.Activity,
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
		entry.resultLines, entry.resultTruncated, entry.resultOmittedLines, entry.resultHeadCount = extractToolResultLines(event.Result)
	}
	if len(entry.detailLines) == 0 && len(entry.resultLines) == 0 && entry.message == "" {
		entry.message = buildToolSummary(event.Activity, "")
	}
	entry.groupKind = buildToolGroupKind(event.Activity)
	if event.Activity != nil {
		entry.activity = event.Activity
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
	entry.groupKind = buildToolGroupKind(event.Activity)
	entry.detailLines = nil
	entry.resultLines, entry.resultTruncated, entry.resultOmittedLines, entry.resultHeadCount = extractToolResultLines(event.Partial)
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
	entry.groupKind = buildToolGroupKind(event.Activity)
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
	if isExplorationGroup(t.toolGroup.order, t.toolGroup.entries) && !hasExplorationErrors(t.toolGroup.order, t.toolGroup.entries) {
		plainText, renderedText := renderExplorationGroup(t.toolGroup.order, t.toolGroup.entries)
		t.replaceBlock(t.toolGroup.index, &toolGroupCell{
			plain:    plainText,
			rendered: renderedText,
		})
		return
	}
	var plain strings.Builder
	var rendered strings.Builder
	prevHadDetail := false
	for _, toolCallID := range t.toolGroup.order {
		entry := t.toolGroup.entries[toolCallID]
		if prevHadDetail {
			plain.WriteByte('\n')
			rendered.WriteByte('\n')
		}
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
			if entry.resultTruncated && idx == entry.resultHeadCount-1 {
				truncMsg := "    ..."
				if entry.resultOmittedLines > 0 {
					truncMsg = fmt.Sprintf("    ... +%d more lines", entry.resultOmittedLines)
				}
				plain.WriteString(truncMsg + "\n")
				rendered.WriteString(lipgloss.NewStyle().Foreground(resultColor).Render(truncMsg) + "\n")
			}
		}
		prevHadDetail = len(entry.detailLines) > 0 || len(entry.resultLines) > 0 || entry.resultTruncated
	}
	t.replaceBlock(t.toolGroup.index, &toolGroupCell{
		plain:    plain.String(),
		rendered: rendered.String(),
	})
}

func buildToolGroupKind(activity *rpc.ToolActivity) string {
	if activity == nil || activity.GroupKind == nil {
		return ""
	}
	return *activity.GroupKind
}

// stripLeadingRenderedMarker removes the "● " prefix that renderCompletedAssistantMarkdown
// prepends to the first content line. The prefix includes ANSI styling, so we strip
// everything up to and including the "● " visible text at the start.
func stripLeadingRenderedMarker(rendered string) string {
	// The marker is rendered with ANSI codes: \x1b[...m● \x1b[0m (or similar).
	// We look for the "● " in the string and strip everything up to and including it.
	const marker = "● "
	idx := strings.Index(rendered, marker)
	if idx < 0 {
		return rendered
	}
	return rendered[idx+len(marker):]
}

func atoiSafe(raw string) int {
	var n int
	fmt.Sscanf(raw, "%d", &n)
	return n
}

