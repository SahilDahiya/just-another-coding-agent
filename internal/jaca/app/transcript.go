package app

import (
	"fmt"
	"net/url"
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

func newTranscriptErrorCell(title string, message string) *rawCell {
	plain := "× " + title + "\n"
	rendered := lipgloss.NewStyle().Foreground(defaultTheme.err).Render("× "+title) + "\n"
	if strings.TrimSpace(message) != "" {
		detail := "  └ " + strings.TrimSpace(message)
		plain += detail + "\n"
		rendered += lipgloss.NewStyle().Foreground(defaultTheme.errSoft).Render(detail) + "\n"
	}
	return &rawCell{plain: plain, rendered: rendered}
}

// assistantCell holds completed assistant markdown with lazy, evictable rendering.
type assistantCell struct {
	plain        string
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

// toolGroupCell holds pre-computed committed tool group text.
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
	completedRuns    []transcriptRun
	currentRunStart  int
	omissionBlockIdx int
	omittedRunCount  int
	renderedCache    string
	renderOffsets    []int
	dirtyFrom        int
	Width            int
	MotionTick       int
}

type transcriptRun struct {
	start int
	end   int
}

const transcriptMaxCompletedRuns = 10

func NewTranscript() *Transcript {
	return &Transcript{
		liveAssistantIdx: -1,
		currentRunStart:  -1,
		omissionBlockIdx: -1,
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
	if t.toolGroup != nil {
		_, activeRendered := t.toolGroup.render(t.MotionTick)
		if t.Width > 0 {
			activeRendered = wrapLines(activeRendered, t.Width)
		}
		rendered.WriteString(activeRendered)
	}

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

func (t *Transcript) WriteStartupBanner(appVersion string, model string, workspaceRoot string, thinking string) {
	titleStyle := lipgloss.NewStyle().Foreground(defaultTheme.text).Bold(true)
	labelStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)
	valueStyle := lipgloss.NewStyle().Foreground(defaultTheme.textSoft)
	hintStyle := lipgloss.NewStyle().Foreground(defaultTheme.textMuted)

	var innerLines []string
	var innerRendered []string

	title := ">_ jaca"
	if appVersion != "" {
		title = fmt.Sprintf(">_ jaca (v%s)", appVersion)
	}
	innerLines = append(innerLines, title, "")
	innerRendered = append(innerRendered, titleStyle.Render(title), "")

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

	t.appendBlock(&rawCell{
		plain:    plainBox + "\n\n",
		rendered: renderedBox + "\n\n",
	})
}

func (t *Transcript) WriteHelp() {
	t.WriteNote("commands", []string{
		"  /help              show this help",
		"  /login <service>   set up ChatGPT subscription login",
		"  /model <name>      switch model",
		"  /trace <mode>      set tracing mode",
		"  /thinking <level>  set thinking level",
		"  /workspace         show workspace root",
		"  /session           show session info",
		"  /name <text>       name active session",
		"  /compact           compact current session",
		"  /new               start a new session",
		"  /quit              exit",
		"",
		"keyboard",
		"  up                 previous prompt",
		"  down               next prompt / restore draft",
		"  ctrl+u             clear prompt",
		"  esc                interrupt active run",
		"  ctrl+c             copy-safe, ctrl+c again quits when idle",
		"",
		"connect",
		"  /login openai-codex                  connect ChatGPT subscription",
		"  /model openai-responses:<model>-chatgpt use ChatGPT subscription models",
		"",
		"advanced",
		"  /auth openai                         prepare OpenAI auth.json setup",
		"  /auth anthropic                      prepare Anthropic auth.json setup",
		"  /auth status                         show auth source per provider",
		"  /auth clear <provider>               clear stored auth.json secret",
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
	t.appendBlock(newNoteCell(title, lines))
}

func (t *Transcript) WriteRenderedNote(title string, plainLines []string, renderedLines []string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	t.appendBlock(newRenderedNoteCell(title, plainLines, renderedLines))
}

func (t *Transcript) InsertNoteBeforeCurrentRun(title string, lines []string) {
	if t.currentRunStart < 0 {
		t.WriteNote(title, lines)
		return
	}
	index := t.currentRunStart
	t.insertBlock(index, newNoteCell(title, lines))
	t.adjustTrackedIndexesAfterInsertion(index)
}

func (t *Transcript) WriteUserTurn(prompt string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	index := t.appendBlock(&rawCell{
		plain:    formatUserTurnPlain(prompt),
		rendered: renderUserTurn(prompt),
	})
	if t.currentRunStart == -1 {
		t.currentRunStart = index
	}
}

func userTurnLines(prompt string) []string {
	lines := strings.Split(strings.TrimRight(prompt, "\n"), "\n")
	if len(lines) == 0 {
		return []string{"│"}
	}
	for i, line := range lines {
		if strings.TrimSpace(line) == "" {
			lines[i] = "│"
			continue
		}
		lines[i] = "│ " + line
	}
	return lines
}

func formatUserTurnPlain(prompt string) string {
	return strings.Join(userTurnLines(prompt), "\n") + "\n"
}

func renderUserTurn(prompt string) string {
	markerStyle := lipgloss.NewStyle().Foreground(defaultTheme.accentSoft).Bold(true)
	textStyle := lipgloss.NewStyle().Foreground(defaultTheme.text).Bold(true)
	lines := userTurnLines(prompt)
	for i, line := range lines {
		content := markerStyle.Render("│")
		if line == "│" {
			lines[i] = content
			continue
		}
		content += markerStyle.Render(" ") + textStyle.Render(strings.TrimPrefix(line, "│ "))
		lines[i] = content
	}
	return strings.Join(lines, "\n") + "\n"
}

func (t *Transcript) WriteLine(line string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.appendBlock(&rawCell{plain: line + "\n", rendered: line + "\n"})
}

func (t *Transcript) WriteError(message string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.appendBlock(newTranscriptErrorCell("Error", message))
	t.finalizeCurrentRun()
}

func (t *Transcript) WriteCompactionCompleted() {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	t.WriteNote("compacted", nil)
}

func (t *Transcript) WriteCompactionWarning(message string) {
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	t.WriteNote("warning", nil)
	t.WriteLine(message)
}

func (t *Transcript) ApplySessionPreview(preview rpc.SessionPreviewResponse) {
	if len(preview.Entries) == 0 {
		return
	}

	if preview.Truncated {
		t.WriteNote("history", []string{"older history omitted"})
	}
	for _, entry := range preview.Entries {
		switch entry.Kind {
		case "instructions":
			t.InsertNoteBeforeCurrentRun("instructions", []string{entry.Text})
		case "user":
			t.WriteUserTurn(entry.Text)
		case "activity":
			t.WriteActivityPreview(entry.Text)
		case "assistant":
			t.completeAssistant(entry.Text)
		case "error":
			t.WriteError(entry.Text)
		}
	}
}

func (t *Transcript) ApplyRunEvent(event rpc.RunEvent) {
	switch event.Type {
	case "session_compaction_completed":
		t.WriteCompactionCompleted()
	case "session_compaction_warning":
		t.WriteCompactionWarning(event.Message)
	case "session_queued_prompt_batch_submitted":
		t.WriteUserTurn(strings.Join(event.Prompts, "\n\n"))
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
		t.endToolGroup()
		if event.ErrorType != "CancelledError" {
			t.appendBlock(newTranscriptErrorCell("Run failed", event.Message))
		}
		t.finalizeCurrentRun()
	case "run_succeeded":
		t.completeRunSucceeded(event)
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
	t.finishAssistant(markdown)
	t.finalizeCurrentRun()
}

func (t *Transcript) finishAssistant(markdown string) {
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

func (t *Transcript) finalizeCurrentRun() {
	if t.currentRunStart < 0 {
		return
	}
	end := len(t.blocks)
	if end <= t.currentRunStart {
		t.currentRunStart = -1
		return
	}
	t.completedRuns = append(t.completedRuns, transcriptRun{
		start: t.currentRunStart,
		end:   end,
	})
	t.currentRunStart = -1
	t.trimCompletedRuns()
}

func (t *Transcript) trimCompletedRuns() {
	excess := len(t.completedRuns) - transcriptMaxCompletedRuns
	if excess <= 0 {
		return
	}

	dropStart := t.completedRuns[0].start
	dropEnd := t.completedRuns[excess-1].end
	if dropStart < 0 || dropEnd > len(t.blocks) || dropStart >= dropEnd {
		return
	}

	removed := dropEnd - dropStart
	t.blocks = append(t.blocks[:dropStart], t.blocks[dropEnd:]...)
	t.invalidateRenderCache(dropStart)

	retained := append([]transcriptRun(nil), t.completedRuns[excess:]...)
	for i := range retained {
		retained[i].start -= removed
		retained[i].end -= removed
	}
	t.completedRuns = retained
	t.adjustTrackedIndexesAfterRemoval(dropStart, removed)

	t.omittedRunCount += excess
	if t.omissionBlockIdx == -1 {
		t.insertBlock(dropStart, newOmissionCell(t.omittedRunCount))
		t.adjustTrackedIndexesAfterInsertion(dropStart)
		t.omissionBlockIdx = dropStart
		return
	}
	t.replaceBlock(t.omissionBlockIdx, newOmissionCell(t.omittedRunCount))
}

func (t *Transcript) insertBlock(index int, block Cell) {
	if index < 0 {
		index = 0
	}
	if index > len(t.blocks) {
		index = len(t.blocks)
	}
	t.blocks = append(t.blocks, nil)
	copy(t.blocks[index+1:], t.blocks[index:])
	t.blocks[index] = block
	t.markDirty(index)
}

func (t *Transcript) invalidateRenderCache(index int) {
	t.renderedCache = ""
	t.renderOffsets = nil
	t.markDirty(index)
}

func (t *Transcript) adjustTrackedIndexesAfterRemoval(start int, removed int) {
	if removed <= 0 {
		return
	}
	if t.liveAssistantIdx >= start {
		t.liveAssistantIdx -= removed
	}
	if t.currentRunStart >= start {
		t.currentRunStart -= removed
	}
	if t.omissionBlockIdx >= start {
		t.omissionBlockIdx -= removed
	}
}

func (t *Transcript) adjustTrackedIndexesAfterInsertion(index int) {
	for i := range t.completedRuns {
		if t.completedRuns[i].start >= index {
			t.completedRuns[i].start++
			t.completedRuns[i].end++
		}
	}
	if t.liveAssistantIdx >= index {
		t.liveAssistantIdx++
	}
	if t.currentRunStart >= index {
		t.currentRunStart++
	}
}

func newOmissionCell(runCount int) *rawCell {
	header := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("note") +
		"  " + lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Bold(true).Render("history")
	line := fmt.Sprintf("older completed runs omitted (%d)", runCount)
	return &rawCell{
		plain:    "note  history\n" + line + "\n\n",
		rendered: header + "\n" + line + "\n\n",
	}
}

func newNoteCell(title string, lines []string) *rawCell {
	return newRenderedNoteCell(title, lines, lines)
}

func newRenderedNoteCell(title string, plainLines []string, renderedLines []string) *rawCell {
	header := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("note") +
		"  " + lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Bold(true).Render(title)
	plain := "note  " + title + "\n"
	rendered := header + "\n"
	for _, line := range plainLines {
		plain += line + "\n"
	}
	for _, line := range renderedLines {
		rendered += line + "\n"
	}
	if len(renderedLines) > 0 || len(plainLines) > 0 {
		rendered += "\n"
		plain += "\n"
	}
	return &rawCell{plain: plain, rendered: rendered}
}

func renderHyperlink(urlText string, label string) string {
	if strings.TrimSpace(urlText) == "" || strings.TrimSpace(label) == "" {
		return label
	}
	return "\x1b]8;;" + urlText + "\x1b\\" + label + "\x1b]8;;\x1b\\"
}

func loginLinkLabel(urlText string) string {
	if strings.TrimSpace(urlText) == "" {
		return "Open login link"
	}
	parsed, err := url.Parse(urlText)
	if err != nil || strings.TrimSpace(parsed.Host) == "" {
		return "Open login link"
	}
	return fmt.Sprintf("Open login link (%s)", parsed.Host)
}

func (t *Transcript) rebuildLiveAssistantRendered() {
	if t.liveAssistantIdx < 0 {
		return
	}
	rc := t.blocks[t.liveAssistantIdx].(*rawCell)
	marker := lipgloss.NewStyle().Foreground(breathingMarkerColor(t.MotionTick)).Render("● ")

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
	if t.liveAssistantIdx >= 0 {
		t.rebuildLiveAssistantRendered()
	}
	if t.toolGroup != nil {
		t.markActiveToolGroupDirty()
	}
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
	hadLiveAssistant := t.liveAssistantIdx >= 0
	t.endLiveAssistant()
	if t.toolGroup != nil && !t.toolGroup.accepts(event) {
		t.endToolGroup()
		t.ensureBlockGap()
	}
	if t.toolGroup == nil {
		if !hadLiveAssistant {
			t.ensureBlockGap()
		}
		t.toolGroup = newToolGroup(buildToolPhase(event.ToolName, event.Activity))
	}
	t.toolGroup.start(event)
	t.markActiveToolGroupDirty()
}

func (t *Transcript) finishTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	if !t.toolGroup.finish(event) {
		return
	}
	t.markActiveToolGroupDirty()
}

func (t *Transcript) updateTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	if !t.toolGroup.update(event) {
		return
	}
	t.markActiveToolGroupDirty()
}

func (t *Transcript) failTool(event rpc.RunEvent) {
	if t.toolGroup == nil {
		return
	}
	if !t.toolGroup.fail(event) {
		return
	}
	t.markActiveToolGroupDirty()
}

func (t *Transcript) endToolGroup() {
	if t.toolGroup == nil {
		return
	}
	plain, rendered := t.toolGroup.render(t.MotionTick)
	t.toolGroup = nil
	t.appendBlock(&toolGroupCell{
		plain:    plain,
		rendered: rendered,
	})
}

func (t *Transcript) markActiveToolGroupDirty() {
	t.markDirty(len(t.blocks))
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
