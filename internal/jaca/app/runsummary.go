package app

import (
	"fmt"
	"hash/fnv"
	"math"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"jaca/internal/jaca/rpc"
)

func (t *Transcript) completeRunSucceeded(event rpc.RunEvent) {
	t.finishAssistant(event.OutputText)
	t.writeRunSeparator(event.TranscriptSummary)
	t.finalizeCurrentRun()
}

func (t *Transcript) WriteActivityPreview(text string) {
	text = strings.TrimSpace(text)
	if text == "" {
		return
	}
	t.endToolGroup()
	t.endLiveAssistant()
	t.ensureBlockGap()
	t.appendBlock(newActivityPreviewCell(text))
}

func (t *Transcript) writeRunSeparator(summary *rpc.RunTranscriptSummary) {
	line := formatRunSeparator(summary)
	if line == "" {
		return
	}
	t.ensureBlockGap()
	t.appendBlock(newRunSeparatorCell(line))
}

func newActivityPreviewCell(text string) *rawCell {
	plain := "* " + text + "\n"
	rendered := lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render("* ") +
		lipgloss.NewStyle().Foreground(defaultTheme.textSoft).Render(text) + "\n"
	return &rawCell{plain: plain, rendered: rendered}
}

func newRunSeparatorCell(line string) *rawCell {
	return &rawCell{
		plain:    line + "\n\n",
		rendered: lipgloss.NewStyle().Foreground(defaultTheme.textMuted).Render(line) + "\n\n",
	}
}

func formatRunSeparator(summary *rpc.RunTranscriptSummary) string {
	if summary == nil || !summary.ShouldShowSeparator {
		return ""
	}

	return "-- jaca " + runSeparatorVerb(summary.ElapsedMS) + " for " + formatSummaryDuration(summary.ElapsedMS) + " --"
}

var runSeparatorVerbs = [...]string{
	"cooked",
	"ate",
	"locked in",
	"crushed",
	"worked",
	"grinded",
	"hustled",
}

func runSeparatorVerb(elapsedMS int) string {
	hasher := fnv.New32a()
	_, _ = hasher.Write([]byte(fmt.Sprintf("%d", elapsedMS)))
	return runSeparatorVerbs[hasher.Sum32()%uint32(len(runSeparatorVerbs))]
}

func formatSummaryDuration(ms int) string {
	if ms < 0 {
		ms = 0
	}
	if ms < 1000 {
		return fmt.Sprintf("%dms", ms)
	}
	if ms < 60000 {
		seconds := float64(ms) / 1000.0
		if ms < 10000 && ms%1000 != 0 {
			return fmt.Sprintf("%.1fs", seconds)
		}
		return fmt.Sprintf("%ds", int(math.Round(seconds)))
	}
	minutes := ms / 60000
	seconds := (ms % 60000) / 1000
	if seconds == 0 {
		return fmt.Sprintf("%dm", minutes)
	}
	return fmt.Sprintf("%dm %ds", minutes, seconds)
}
