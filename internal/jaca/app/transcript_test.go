package app

import (
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

	transcript := NewTranscript()
	transcript.WriteStartupBanner("ollama:test", "/workspace", "medium")

	plain := transcript.blocks[0].plain
	if !strings.Contains(plain, "jaca  /workspace  |  model ollama:test  |  thinking medium") {
		t.Fatalf("plain banner missing headline: %q", plain)
	}
	if !strings.Contains(plain, "ollama http://localhost:11434/v1") {
		t.Fatalf("plain banner missing ollama line: %q", plain)
	}
	if !strings.Contains(plain, "local ollama, no key needed") {
		t.Fatalf("plain banner missing local hint: %q", plain)
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
	if !strings.Contains(plain, "use /provider openai <key>") {
		t.Fatalf("plain banner missing provider guidance: %q", plain)
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
		ToolName:   "bash",
		ToolCallID: "call-1",
		Args:       map[string]any{"command": "git status --short"},
	})
	duration := 17
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "bash",
		ToolCallID: "call-1",
		Result:     map[string]any{"output": "clean"},
		Activity:   &rpc.ToolActivity{DurationMS: &duration},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, "error") {
		t.Fatalf("tool row incorrectly rendered as error: %q", plain)
	}
	if !strings.Contains(plain, "● bash  git status --short  ok 17ms") {
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
	for _, want := range []string{
		"● read  agents.md  ok 15ms",
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
		"● edit  src/app.go  ok 83ms",
		"  Update(src/app.go)",
		"  │ Added 3 lines, removed 1 line",
		"  │ @@ -10,2 +10,4 @@",
		"  │ 10   line a",
		"  │ 11 - line b",
		"  │ 11 + line c",
		"  │ 12 + line d",
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
		ToolName:   "bash",
		ToolCallID: "call-bash",
		Args:       map[string]any{"command": "python - <<'PY'"},
	})
	updateDuration := 250
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_updated",
		ToolName:   "bash",
		ToolCallID: "call-bash",
		Partial:    map[string]any{"output": "one\ntwo\n"},
		Activity: &rpc.ToolActivity{
			Title:      "bash python - <<'PY'",
			Summary:    strPtr("command still running"),
			DurationMS: &updateDuration,
		},
	})

	plain := transcript.blocks[len(transcript.blocks)-1].plain
	for _, want := range []string{
		"● bash  python - <<'PY'  running  command still running  250ms",
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
		ToolName:   "bash",
		ToolCallID: "call-bash",
		Result:     map[string]any{"exit_code": 0, "output": "done"},
		Activity: &rpc.ToolActivity{
			Title:      "bash python - <<'PY'",
			Summary:    strPtr("command exited 0"),
			DurationMS: &finalDuration,
		},
	})

	plain = transcript.blocks[len(transcript.blocks)-1].plain
	if strings.Contains(plain, "command still running") || strings.Contains(plain, "  └ one") {
		t.Fatalf("final tool row kept live partial output: %q", plain)
	}
	for _, want := range []string{
		"● bash  python - <<'PY'  ok 500ms",
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
		ToolName:   "bash",
		ToolCallID: "call-bash",
		Args:       map[string]any{"command": "printf"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "bash",
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

func strPtr(value string) *string {
	return &value
}
