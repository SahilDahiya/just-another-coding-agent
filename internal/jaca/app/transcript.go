package app

import (
	"fmt"
	"os"
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

type Transcript struct {
	blocks           []transcriptBlock
	liveAssistantIdx int
	liveDeltaBuf     strings.Builder
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
		blockRendered := t.blocks[i].rendered
		if blockRendered == "" {
			blockRendered = renderTranscriptBlock(t.blocks[i])
			t.blocks[i].rendered = blockRendered
		}
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
		t.liveDeltaBuf.Reset()
		t.liveDeltaBuf.WriteString(delta)
		t.liveAssistantIdx = t.appendBlock(transcriptBlock{
			plain: delta,
		})
		t.rebuildLiveAssistantRendered()
		return
	}
	t.liveDeltaBuf.WriteString(delta)
	t.blocks[t.liveAssistantIdx].plain = t.liveDeltaBuf.String()
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
	t.liveDeltaBuf.Reset()
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
		toolName:  event.ToolName,
		preview:   buildToolPreview(event.ToolName, event.Args, event.ArgsValid, event.Activity),
		groupKind: buildToolGroupKind(event.Activity),
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
	t.replaceBlock(t.toolGroup.index, transcriptBlock{
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

func atoiSafe(raw string) int {
	var n int
	fmt.Sscanf(raw, "%d", &n)
	return n
}

func renderTranscriptBlock(block transcriptBlock) string {
	switch block.kind {
	case transcriptBlockAssistantMarkdown:
		return renderCompletedAssistantMarkdown(strings.TrimSuffix(block.plain, "\n"))
	default:
		return block.plain
	}
}
