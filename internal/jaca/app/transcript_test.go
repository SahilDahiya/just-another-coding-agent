package app

import (
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"jaca/internal/jaca/rpc"
)

var ansiRe = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func stripANSI(text string) string {
	return ansiRe.ReplaceAllString(text, "")
}

func TestWriteStartupBannerIncludesOllamaHintsInPlainText(t *testing.T) {
	t.Setenv("OLLAMA_BASE_URL", "")
	workspaceRoot := filepath.Join("workspace", "repo")

	transcript := NewTranscript()
	transcript.WriteStartupBanner("ollama:test", workspaceRoot, "medium")

	plain := transcript.blocks[0].plain
	if !strings.Contains(plain, ">_ jaca") {
		t.Fatalf("plain banner missing title: %q", plain)
	}
	if !strings.Contains(plain, "model:     ollama:test") {
		t.Fatalf("plain banner missing model: %q", plain)
	}
	if !strings.Contains(plain, "directory: "+displayPath(workspaceRoot)) {
		t.Fatalf("plain banner missing directory: %q", plain)
	}
}

func TestWriteStartupBannerShowsProviderGuidanceForMissingOpenAIKey(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "")

	transcript := NewTranscript()
	transcript.WriteStartupBanner("openai:gpt-5.4", "/workspace", "")

	plain := transcript.blocks[0].plain
	if !strings.Contains(plain, "no OPENAI_API_KEY") {
		t.Fatalf("plain banner missing missing-key warning: %q", plain)
	}
	if !strings.Contains(plain, "use /provider openai") {
		t.Fatalf("plain banner missing provider guidance: %q", plain)
	}
	if strings.Contains(plain, "<key>") {
		t.Fatalf("plain banner still teaches secret-on-command guidance: %q", plain)
	}
}

func TestCompletedAssistantMarkdownAvoidsBackgroundFills(t *testing.T) {
	markdown := "## Review\n\n- first point\n- second point\n\n1. step one\n2. step two\n\n`inline code`"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	if strings.Contains(rendered, "[48;") {
		t.Fatalf("rendered markdown contains background ANSI codes: %q", rendered)
	}

	plainRendered := stripANSI(rendered)
	if strings.Contains(plainRendered, "- first point") {
		t.Fatalf("rendered markdown kept unordered markdown bullets: %q", plainRendered)
	}
	if !strings.Contains(plainRendered, "    first point") {
		t.Fatalf("rendered markdown missing indented unordered item: %q", plainRendered)
	}
	if !strings.Contains(plainRendered, "  1. step one") {
		t.Fatalf("rendered markdown missing ordered item: %q", plainRendered)
	}
	if !strings.Contains(plainRendered, "inline code") {
		t.Fatalf("rendered markdown missing inline code text: %q", plainRendered)
	}
}

func TestToolSuccessDoesNotTreatResultMapWithoutOkAsError(t *testing.T) {
	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-1",
		Args:       map[string]any{"command": "git status --short"},
	})
	duration := 17
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-1",
		Result:     map[string]any{"output": "clean"},
		Activity:   &rpc.ToolActivity{DurationMS: &duration},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, "error") {
		t.Fatalf("tool row incorrectly rendered as error: %q", plain)
	}
	if !strings.Contains(plain, "shell  git status --short  ok 17ms") {
		t.Fatalf("tool row missing success state: %q", plain)
	}
}

func TestOperationalToolResultRendersAsNeutralOutput(t *testing.T) {
	transcript := NewTranscript()
	duration := 15
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "call-read",
		Args:       map[string]any{"path": "agents.md"},
		Activity:   &rpc.ToolActivity{Title: "read agents.md"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "call-read",
		Result: map[string]any{
			"ok":      false,
			"message": "No such file or directory: '/workspace/agents.md'",
		},
		Activity: &rpc.ToolActivity{
			Title:      "read agents.md",
			Summary:    strPtr("No such file or directory: '/workspace/agents.md'"),
			DurationMS: &duration,
		},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, "error") {
		t.Fatalf("operational tool result rendered as error: %q", plain)
	}
	if strings.Contains(plain, " ok ") {
		t.Fatalf("operational miss rendered as ok: %q", plain)
	}
	for _, want := range []string{
		"● read  agents.md  15ms",
		"  └ No such file or directory: '/workspace/agents.md'",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("operational tool result missing %q in %q", want, plain)
		}
	}
}

func TestEditToolRowsRenderStructuredDiffPreview(t *testing.T) {
	duration := 83
	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "edit",
		ToolCallID: "call-edit",
		Args:       map[string]any{"path": "src/app.go"},
		Activity:   &rpc.ToolActivity{Title: "edit src/app.go"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "edit",
		ToolCallID: "call-edit",
		Result:     "edited src/app.go",
		Activity: &rpc.ToolActivity{
			Title:      "edit src/app.go",
			Summary:    strPtr("edit applied"),
			DurationMS: &duration,
			Details: map[string]any{
				"kind":          "edit",
				"path":          "src/app.go",
				"added_lines":   3,
				"removed_lines": 1,
				"diff": "" +
					"--- src/app.go\n" +
					"+++ src/app.go\n" +
					"@@ -10,2 +10,4 @@\n" +
					" line a\n" +
					"-line b\n" +
					"+line c\n" +
					"+line d\n",
			},
		},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	for _, want := range []string{
		"edit  src/app.go  ok 83ms",
		"  Update(src/app.go)",
		"Added 3 lines, removed 1 line",
		"@@ -10,2 +10,4 @@",
		"10   line a",
		"11 - line b",
		"11 + line c",
		"12 + line d",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("plain diff preview missing %q in %q", want, plain)
		}
	}
}

func TestToolUpdateRendersLivePartialOutputAndFinalResult(t *testing.T) {
	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-bash",
		Args:       map[string]any{"command": "python - <<'PY'"},
	})
	updateDuration := 250
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_updated",
		ToolName:   "shell",
		ToolCallID: "call-bash",
		Partial:    map[string]any{"output": "one\ntwo\n"},
		Activity: &rpc.ToolActivity{
			Title:      "shell python - <<'PY'",
			Summary:    strPtr("command still running"),
			DurationMS: &updateDuration,
		},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	for _, want := range []string{
		"● shell  python - <<'PY'  running  command still running  250ms",
		"  └ one",
		"    two",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("live tool update missing %q in %q", want, plain)
		}
	}

	finalDuration := 500
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-bash",
		Result:     map[string]any{"exit_code": 0, "output": "done"},
		Activity: &rpc.ToolActivity{
			Title:      "shell python - <<'PY'",
			Summary:    strPtr("command exited 0"),
			DurationMS: &finalDuration,
		},
	})

	plain = transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, "command still running") || strings.Contains(plain, "  └ one") {
		t.Fatalf("final tool row kept live partial output: %q", plain)
	}
	for _, want := range []string{
		"● shell  python - <<'PY'  ok 500ms",
		"  └ done",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("final tool result missing %q in %q", want, plain)
		}
	}
}

func TestRenderOnlyInvalidatesFromFirstDirtyRow(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteLine("first")
	transcript.WriteLine("second")

	initial := transcript.Render()
	if transcript.dirtyFrom != -1 {
		t.Fatalf("dirtyFrom = %d, want -1 after render", transcript.dirtyFrom)
	}
	if len(transcript.renderOffsets) != len(transcript.blocks)+1 {
		t.Fatalf("renderOffsets len = %d, want %d", len(transcript.renderOffsets), len(transcript.blocks)+1)
	}

	transcript.WriteLine("third")
	if transcript.dirtyFrom != 2 {
		t.Fatalf("dirtyFrom after append = %d, want 2", transcript.dirtyFrom)
	}

	rendered := transcript.Render()
	if rendered != initial+"third\n" {
		t.Fatalf("Render() after append = %q, want %q", rendered, initial+"third\n")
	}
	if transcript.renderOffsets[2] != len(initial) {
		t.Fatalf("renderOffsets[2] = %d, want %d", transcript.renderOffsets[2], len(initial))
	}

	transcript.replaceBlock(1, transcriptBlock{
		plain:    "SECOND\n",
		rendered: "SECOND\n",
	})
	if transcript.dirtyFrom != 1 {
		t.Fatalf("dirtyFrom after replace = %d, want 1", transcript.dirtyFrom)
	}

	rendered = transcript.Render()
	if !strings.Contains(rendered, "SECOND\nthird\n") {
		t.Fatalf("Render() after replace = %q, want updated suffix", rendered)
	}
}

func TestRenderEvictsRenderedCacheForImmutableCompletedAssistantBlocks(t *testing.T) {
	transcript := NewTranscript()
	markdown := "## Review\n\nThis is a long completed assistant message."
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	if transcript.blocks[0].rendered == "" {
		t.Fatal("completed assistant block rendered cache unexpectedly empty before render")
	}

	rendered := transcript.Render()
	if !strings.Contains(stripANSI(rendered), "This is a long completed assistant message.") {
		t.Fatalf("Render() missing completed assistant text: %q", rendered)
	}
	if transcript.blocks[0].rendered != "" {
		t.Fatalf("completed assistant block kept rendered cache after render: %q", transcript.blocks[0].rendered)
	}

	transcript.WriteLine("tail")
	rendered = transcript.Render()
	plainRendered := stripANSI(rendered)
	if !strings.Contains(plainRendered, "This is a long completed assistant message.") || !strings.Contains(plainRendered, "tail") {
		t.Fatalf("Render() after append lost cached transcript prefix: %q", plainRendered)
	}
	if transcript.blocks[0].rendered != "" {
		t.Fatalf("completed assistant block rendered cache should stay evicted after append: %q", transcript.blocks[0].rendered)
	}
}

func TestToolResultLinesTruncateVeryLongDisplayLines(t *testing.T) {
	transcript := NewTranscript()
	longLine := strings.Repeat("x", 240)

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-bash",
		Args:       map[string]any{"command": "printf"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-bash",
		Result:     map[string]any{"output": longLine},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, longLine) {
		t.Fatalf("tool row kept unbounded long output line: %q", plain)
	}
	if !strings.Contains(plain, strings.Repeat("x", 32)+"...") {
		t.Fatalf("tool row missing truncated preview marker: %q", plain)
	}
}

func TestCodeBlockLanguageLabelRendered(t *testing.T) {
	markdown := "Here is code:\n\n```python\nprint('hello')\n```\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "python") {
		t.Fatalf("rendered code block missing language label: %q", plain)
	}
	if !strings.Contains(plain, "print('hello')") {
		t.Fatalf("rendered code block missing code content: %q", plain)
	}
}

func TestBlockquoteRendered(t *testing.T) {
	markdown := "> This is a quote\n>> Nested quote\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "│") {
		t.Fatalf("rendered blockquote missing bar prefix: %q", plain)
	}
	if !strings.Contains(plain, "This is a quote") {
		t.Fatalf("rendered blockquote missing content: %q", plain)
	}
	if !strings.Contains(plain, "Nested quote") {
		t.Fatalf("rendered nested blockquote missing content: %q", plain)
	}
	// Nested should have two bars
	nestedIdx := strings.Index(plain, "Nested quote")
	beforeNested := plain[:nestedIdx]
	lastNewline := strings.LastIndex(beforeNested, "\n")
	if lastNewline < 0 {
		lastNewline = 0
	}
	linePrefix := beforeNested[lastNewline:]
	barCount := strings.Count(linePrefix, "│")
	if barCount < 2 {
		t.Fatalf("nested blockquote expected 2 bars, got %d in prefix %q", barCount, linePrefix)
	}
}

func TestStrikethroughRendered(t *testing.T) {
	markdown := "This has ~~deleted text~~ in it.\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "deleted text") {
		t.Fatalf("rendered strikethrough missing content: %q", plain)
	}
	// The ~~ markers should be stripped
	if strings.Contains(plain, "~~") {
		t.Fatalf("rendered strikethrough still has ~~ markers: %q", plain)
	}
}

func TestHorizontalRuleRendered(t *testing.T) {
	markdown := "Above\n\n---\n\nBelow\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "─") {
		t.Fatalf("rendered horizontal rule missing rule character: %q", plain)
	}
	if !strings.Contains(plain, "Above") || !strings.Contains(plain, "Below") {
		t.Fatalf("rendered horizontal rule missing surrounding text: %q", plain)
	}
}

func TestCodeBlockWithoutLanguageHasNoLabel(t *testing.T) {
	markdown := "```\nplain code\n```\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].rendered
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "plain code") {
		t.Fatalf("rendered code block missing content: %q", plain)
	}
}

func TestWrapLinesPlainText(t *testing.T) {
	input := "the quick brown fox jumps over the lazy dog"
	got := wrapLines(input, 20)
	lines := strings.Split(got, "\n")
	for i, line := range lines {
		if visibleLen(line) > 20 {
			t.Fatalf("line %d exceeds width 20: %q (visible=%d)", i, line, visibleLen(line))
		}
	}
	// Rejoin wrapped lines and normalize spaces to verify no content lost.
	rejoined := strings.Join(strings.Fields(strings.ReplaceAll(got, "\n", " ")), " ")
	if rejoined != "the quick brown fox jumps over the lazy dog" {
		t.Fatalf("wrapLines lost content: %q", got)
	}
}

func TestWrapLinesRespectsIndentation(t *testing.T) {
	input := "    indented line that is much longer than the wrap width limit"
	got := wrapLines(input, 30)
	for i, line := range strings.Split(got, "\n") {
		if visibleLen(line) > 30 {
			t.Fatalf("line %d exceeds width 30: %q", i, line)
		}
		if i > 0 && !strings.HasPrefix(line, "    ") {
			t.Fatalf("continuation line %d lost indent: %q", i, line)
		}
	}
}

func TestWrapLinesPreservesANSI(t *testing.T) {
	input := "\x1b[31mred word\x1b[0m and \x1b[32mgreen word\x1b[0m plus extra padding text here"
	got := wrapLines(input, 25)
	// All ANSI escapes should survive.
	if !strings.Contains(got, "\x1b[31m") || !strings.Contains(got, "\x1b[32m") {
		t.Fatalf("wrapLines stripped ANSI escapes: %q", got)
	}
	for i, line := range strings.Split(got, "\n") {
		if visibleLen(line) > 25 {
			t.Fatalf("line %d exceeds width 25: %q (visible=%d)", i, line, visibleLen(line))
		}
	}
}

func TestWrapLinesNoOpWhenWidthZero(t *testing.T) {
	input := "should not be wrapped at all even though it is very long"
	got := wrapLines(input, 0)
	if got != input {
		t.Fatalf("wrapLines modified text when width=0: %q", got)
	}
}

func TestWrapLinesShortLineUnchanged(t *testing.T) {
	input := "short"
	got := wrapLines(input, 80)
	if got != input {
		t.Fatalf("wrapLines modified short line: %q", got)
	}
}

func TestRenderAppliesWrapWhenWidthSet(t *testing.T) {
	transcript := NewTranscript()
	transcript.Width = 30
	long := strings.Repeat("word ", 20)
	transcript.WriteLine(long)
	rendered := transcript.Render()
	for i, line := range strings.Split(rendered, "\n") {
		if visibleLen(line) > 30 {
			t.Fatalf("Render() line %d exceeds width 30: %q (visible=%d)", i, line, visibleLen(line))
		}
	}
	// Content should still be present.
	plain := strings.ReplaceAll(rendered, "\n", " ")
	if !strings.Contains(plain, "word") {
		t.Fatalf("Render() lost content after wrapping: %q", rendered)
	}
}

func TestRenderNoWrapWhenWidthZero(t *testing.T) {
	transcript := NewTranscript()
	// Width defaults to 0
	long := strings.Repeat("word ", 20)
	transcript.WriteLine(long)
	rendered := transcript.Render()
	if strings.Count(rendered, "\n") > 1 {
		t.Fatalf("Render() wrapped when Width=0: %q", rendered)
	}
}

func strPtr(value string) *string {
	return &value
}
