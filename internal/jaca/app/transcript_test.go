package app

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"

	"jaca/internal/jaca/rpc"
)

var ansiRe = regexp.MustCompile(`\x1b\[[0-9;]*m`)
var osc8Re = regexp.MustCompile(`\x1b]8;;[^\x1b]*\x1b\\`)

func stripANSI(text string) string {
	text = ansiRe.ReplaceAllString(text, "")
	return osc8Re.ReplaceAllString(text, "")
}

func transcriptPlain(transcript *Transcript) string {
	return stripANSI(transcript.Render())
}

func TestWriteStartupBannerIncludesModelInPlainText(t *testing.T) {
	workspaceRoot := filepath.Join("workspace", "repo")

	transcript := NewTranscript()
	transcript.WriteStartupBanner("0.1.0", "openai-responses:gpt-5.4", workspaceRoot, "medium")

	plain := transcript.blocks[0].Plain()
	if !strings.Contains(plain, ">_ jaca (v0.1.0)") {
		t.Fatalf("plain banner missing title: %q", plain)
	}
	if !strings.Contains(plain, "model:     gpt-5.4 | api") {
		t.Fatalf("plain banner missing model: %q", plain)
	}
	if !strings.Contains(plain, "directory: "+displayPath(workspaceRoot)) {
		t.Fatalf("plain banner missing directory: %q", plain)
	}
}

func TestWriteStartupBannerDoesNotGuessCredentialStateFromEnvironment(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteStartupBanner("0.1.0", "openai:gpt-5.4", "/workspace", "")

	plain := transcript.blocks[0].Plain()
	if strings.Contains(plain, "API_KEY") {
		t.Fatalf("startup banner should not guess secret state from env: %q", plain)
	}
}

func TestWriteUserTurnUsesSpeakerRail(t *testing.T) {
	original := lipgloss.ColorProfile()
	defer lipgloss.SetColorProfile(original)
	lipgloss.SetColorProfile(termenv.TrueColor)

	transcript := NewTranscript()
	transcript.Width = 40
	transcript.WriteUserTurn("review changes\nthen run tests")

	rendered := transcript.Render()
	plain := stripANSI(rendered)
	for _, want := range []string{
		"│ review changes",
		"│ then run tests",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("user turn missing %q in %q", want, plain)
		}
	}
	if !strings.Contains(rendered, "[48;") {
		t.Fatalf("user turn should use subtle background fill: %q", rendered)
	}
}

func TestRenderReflowsWhenWidthChanges(t *testing.T) {
	transcript := NewTranscript()
	transcript.Width = 24
	transcript.WriteUserTurn("review deeply nested changes and then run targeted tests")

	narrow := transcript.Render()
	transcript.Width = 52
	wide := transcript.Render()

	if narrow == wide {
		t.Fatalf("Render() did not update after width change: %q", narrow)
	}
	for i, line := range strings.Split(wide, "\n") {
		if visibleLen(line) > 52 {
			t.Fatalf("Render() line %d exceeds width 52 after resize: %q (visible=%d)", i, line, visibleLen(line))
		}
	}
}

func TestCompletedAssistantMarkdownAvoidsBackgroundFills(t *testing.T) {
	markdown := "## Review\n\n- first point\n- second point\n\n1. step one\n2. step two\n\n`inline code`"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
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

	plain := transcriptPlain(transcript)
	if strings.Contains(plain, "error") {
		t.Fatalf("tool row incorrectly rendered as error: %q", plain)
	}
	if !strings.Contains(plain, "shell  git status --short  ok  17ms") {
		t.Fatalf("tool row missing success state: %q", plain)
	}
	groupPlain, groupRendered := transcript.toolGroup.render(0)
	if groupPlain != stripANSI(groupRendered) {
		t.Fatalf("tool group plain/render spacing mismatch: plain=%q rendered=%q", groupPlain, stripANSI(groupRendered))
	}
}

func TestToolFailureRendersStructuredErrorRow(t *testing.T) {
	transcript := NewTranscript()
	duration := 70
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Args:       map[string]any{"command": "git status --short"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_failed",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Message:    "Tool update must match a pending tool_call_started: call_123",
		Activity: &rpc.ToolActivity{
			DurationMS: &duration,
		},
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"× Shell failed 70ms",
		"  └ Tool update must match a pending tool_call_started: call_123",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("tool failure row missing %q in %q", want, plain)
		}
	}
	if strings.Contains(plain, "error  ") {
		t.Fatalf("tool failure row kept flat error copy in %q", plain)
	}
}

func TestSubagentRowsRenderBoundedPreviewBlock(t *testing.T) {
	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "subagent",
		ToolCallID: "call-subagent",
		Args: map[string]any{
			"name": "compaction-scan",
			"role": "explore",
			"task": "Find where compaction resets turn context.",
		},
		Activity: &rpc.ToolActivity{
			Title:        "subagent compaction-scan",
			DisplayLabel: strPtr("Explore"),
		},
	})

	runningDuration := 14
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_updated",
		ToolName:   "subagent",
		ToolCallID: "call-subagent",
		Activity: &rpc.ToolActivity{
			Title:        "subagent compaction-scan",
			DisplayLabel: strPtr("Explore"),
			Summary:      strPtr("exploring repository"),
			DurationMS:   &runningDuration,
			Details: map[string]any{
				"kind":             "subagent",
				"name":             "compaction-scan",
				"role":             "explore",
				"preview_lines":    []any{"read AGENTS.md"},
				"preview_terminal": false,
			},
		},
	})

	runningPlain := transcriptPlain(transcript)
	for _, want := range []string{
		"● subagent  compaction-scan  running  exploring repository  14ms",
		"  │ read AGENTS.md",
	} {
		if !strings.Contains(runningPlain, want) {
			t.Fatalf("running subagent block missing %q in %q", want, runningPlain)
		}
	}

	finalDuration := 61
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "subagent",
		ToolCallID: "call-subagent",
		Result: map[string]any{
			"ok":          true,
			"name":        "compaction-scan",
			"role":        "explore",
			"output_text": "Found reset in runtime/compaction/resume.py",
		},
		Activity: &rpc.ToolActivity{
			Title:        "subagent compaction-scan",
			DisplayLabel: strPtr("Explore"),
			Summary:      strPtr("Found reset in runtime/compaction/resume.py"),
			DurationMS:   &finalDuration,
			Details: map[string]any{
				"kind": "subagent",
				"name": "compaction-scan",
				"role": "explore",
				"preview_lines": []any{
					"read AGENTS.md",
					"Found reset in runtime/compaction/resume.py",
				},
				"preview_terminal": true,
			},
		},
	})

	finalPlain := transcriptPlain(transcript)
	for _, want := range []string{
		"● subagent  compaction-scan  ok  61ms",
		"  │ read AGENTS.md",
		"  └ Found reset in runtime/compaction/resume.py",
	} {
		if !strings.Contains(finalPlain, want) {
			t.Fatalf("final subagent block missing %q in %q", want, finalPlain)
		}
	}
	if strings.Contains(finalPlain, "output_text") {
		t.Fatalf("subagent result spilled raw payload into transcript: %q", finalPlain)
	}
}

func TestTeachingPacketRowsRenderSnippetBlocks(t *testing.T) {
	transcript := NewTranscript()
	duration := 42
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "publish_teaching_packet",
		ToolCallID: "call-packet",
		Args: map[string]any{
			"title": "Slash command dispatch",
			"snippets": []any{
				map[string]any{
					"path":       "internal/jaca/app/slash.go",
					"start_line": 1,
					"end_line":   2,
				},
			},
		},
		Activity: &rpc.ToolActivity{
			Title:        "publish_teaching_packet Slash command dispatch",
			DisplayLabel: strPtr("Teach"),
		},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "publish_teaching_packet",
		ToolCallID: "call-packet",
		Result: map[string]any{
			"title":         "Slash command dispatch",
			"snippet_count": 1,
		},
		Activity: &rpc.ToolActivity{
			Title:        "Slash command dispatch",
			DisplayLabel: strPtr("Teach"),
			Summary:      strPtr("showing 1 snippet"),
			DurationMS:   &duration,
			Details: map[string]any{
				"kind": "teaching_packet",
				"snippets": []any{
					map[string]any{
						"path":       "internal/jaca/app/slash.go",
						"start_line": 1,
						"end_line":   2,
						"text":       "package app\nvar slashCommands = []string{\"/onboard\"}",
					},
				},
			},
		},
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"publish_teaching_packet  Slash command dispatch  ok  42ms",
		"  Snippet(internal/jaca/app/slash.go:1-2)",
		"  │ package app",
		"  │ var slashCommands = []string{\"/onboard\"}",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("teaching packet render missing %q in %q", want, plain)
		}
	}
}

func TestRunFailureRendersStructuredErrorRow(t *testing.T) {
	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:    "run_failed",
		Message: "Tool update must match a pending tool_call_started: call_123",
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"× Run failed",
		"  └ Tool update must match a pending tool_call_started: call_123",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("run failure row missing %q in %q", want, plain)
		}
	}
	if strings.Contains(plain, "error  ") {
		t.Fatalf("run failure row kept flat error copy in %q", plain)
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

	plain := transcriptPlain(transcript)
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

func TestDeniedToolResultRendersAsDeepRedIndicator(t *testing.T) {
	transcript := NewTranscript()
	duration := 22
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Args:       map[string]any{"command": "curl https://example.com"},
		Activity:   &rpc.ToolActivity{Title: "shell curl https://example.com"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Result: map[string]any{
			"ok":          false,
			"outcome":     "denied",
			"denial_type": "approval_denied",
			"message":     "Approval denied: allow shell command: curl https://example.com. The command was not run.",
		},
		Activity: &rpc.ToolActivity{
			Title:      "shell curl https://example.com",
			Summary:    strPtr("Approval denied: allow shell command: curl https://example.com. The command was not run."),
			DurationMS: &duration,
		},
	})

	plain := transcriptPlain(transcript)
	if strings.Contains(plain, "error") {
		t.Fatalf("denied tool result rendered as error: %q", plain)
	}
	for _, want := range []string{
		"● shell  curl https://example.com  denied  22ms",
		"  └ Approval denied: allow shell command: curl https://example.com. The command was not run.",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("denied tool result missing %q in %q", want, plain)
		}
	}
}

func TestExplorationToolRowsUseSofterPreviewStyling(t *testing.T) {
	original := lipgloss.ColorProfile()
	t.Cleanup(func() {
		lipgloss.SetColorProfile(original)
	})
	lipgloss.SetColorProfile(termenv.TrueColor)

	normal := renderToolActivityLine(&toolEntry{
		toolName: "read",
		preview:  "AGENTS.md",
	}, 0)
	exploration := renderToolActivityLine(&toolEntry{
		toolName:  "read",
		preview:   "AGENTS.md",
		groupKind: "exploration",
	}, 0)

	if normal == exploration {
		t.Fatalf("expected exploration row styling to differ from normal row: %q", exploration)
	}
	if !strings.Contains(exploration, "AGENTS.md") {
		t.Fatalf("exploration row missing preview: %q", exploration)
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

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"edit  src/app.go  ok  83ms",
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

	plain := transcriptPlain(transcript)
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

	plain = transcriptPlain(transcript)
	if strings.Contains(plain, "command still running") || strings.Contains(plain, "  └ one") {
		t.Fatalf("final tool row kept live partial output: %q", plain)
	}
	for _, want := range []string{
		"● shell  python - <<'PY'  ok  500ms",
		"  └ done",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("final tool result missing %q in %q", want, plain)
		}
	}
}

func TestToolGroupRendersActiveUntilPhaseEnds(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteUserTurn("inspect repo")
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Args:       map[string]any{"command": "git status --short"},
	})
	duration := 12
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-shell",
		Result:     map[string]any{"output": "clean"},
		Activity:   &rpc.ToolActivity{DurationMS: &duration},
	})

	if transcript.toolGroup == nil {
		t.Fatal("tool group should stay active before the next transcript phase")
	}
	for _, block := range transcript.blocks {
		if strings.Contains(block.Plain(), "git status --short") {
			t.Fatalf("active tool group was committed early in block %q", block.Plain())
		}
	}
	plain := transcriptPlain(transcript)
	if !strings.Contains(plain, "shell  git status --short  ok  12ms") {
		t.Fatalf("active tool group missing from render: %q", plain)
	}

	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "done"})

	if transcript.toolGroup != nil {
		t.Fatal("tool group should commit when assistant text starts")
	}
	committed := false
	for _, block := range transcript.blocks {
		if strings.Contains(block.Plain(), "git status --short") {
			committed = true
		}
	}
	if !committed {
		t.Fatalf("tool group was not committed after phase transition: %q", transcriptPlain(transcript))
	}
}

func TestSessionCompactionLifecycleEventsRenderInTranscript(t *testing.T) {
	transcript := NewTranscript()

	transcript.ApplyRunEvent(rpc.RunEvent{Type: "session_compaction_started"})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:              "session_compaction_completed",
		CompactionID:      "compact-1",
		SummarizedThrough: "run-5",
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:    "session_compaction_warning",
		Message: "Session has been compacted multiple times; continuity quality may degrade.",
	})

	plain := stripANSI(transcript.Render())
	for _, want := range []string{
		"note  compacted",
		"note  warning",
		"Session has been compacted multiple times; continuity quality may degrade.",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("compaction transcript missing %q in %q", want, plain)
		}
	}
	for _, absent := range []string{
		"compacting session...",
		"session compacted",
	} {
		if strings.Contains(plain, absent) {
			t.Fatalf("compaction transcript should not keep legacy line %q in %q", absent, plain)
		}
	}
	if strings.Contains(plain, "note  compact\n") {
		t.Fatalf("compaction transcript should not keep legacy compact note in %q", plain)
	}
}

func TestExplorationGroupRendersCoalescedExploredBlock(t *testing.T) {
	transcript := NewTranscript()

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-1",
		Args:       map[string]any{"path": "/workspace/README.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "README.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-1",
		Result:     "read README",
		Activity:   explorationActivity("read", map[string]any{"short_path": "README.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-2",
		Args:       map[string]any{"path": "/workspace/AGENTS.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-2",
		Result:     "read AGENTS",
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "ls",
		ToolCallID: "ls-1",
		Args:       map[string]any{"path": "/workspace/src"},
		Activity:   explorationActivity("ls", map[string]any{"short_path": "src"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "ls",
		ToolCallID: "ls-1",
		Result:     "listed src",
		Activity:   explorationActivity("ls", map[string]any{"short_path": "src"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "grep",
		ToolCallID: "grep-1",
		Args:       map[string]any{"pattern": "build_canonical_agent(", "path": "/workspace/tests"},
		Activity:   explorationActivity("grep", map[string]any{"pattern": "build_canonical_agent(", "short_path": "tests"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "grep",
		ToolCallID: "grep-1",
		Result:     "matched tests",
		Activity:   explorationActivity("grep", map[string]any{"pattern": "build_canonical_agent(", "short_path": "tests"}),
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"● Read/Searched (4) ok",
		"  └ Read README.md, AGENTS.md",
		"    List src",
		"    Search build_canonical_agent( in tests",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("exploration group missing %q in %q", want, plain)
		}
	}
}

func TestExplorationGroupDeduplicatesRepeatedReads(t *testing.T) {
	transcript := NewTranscript()

	// Read README.md twice consecutively — should appear once in the coalesced line.
	for _, id := range []string{"read-1", "read-2"} {
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_started",
			ToolName:   "read",
			ToolCallID: id,
			Args:       map[string]any{"path": "/workspace/README.md"},
			Activity:   explorationActivity("read", map[string]any{"short_path": "README.md"}),
		})
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_succeeded",
			ToolName:   "read",
			ToolCallID: id,
			Result:     "content",
			Activity:   explorationActivity("read", map[string]any{"short_path": "README.md"}),
		})
	}
	// Read AGENTS.md once.
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-3",
		Args:       map[string]any{"path": "/workspace/AGENTS.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-3",
		Result:     "content",
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})

	plain := transcriptPlain(transcript)
	want := "Read README.md, AGENTS.md"
	if !strings.Contains(plain, want) {
		t.Fatalf("expected deduplicated %q in %q", want, plain)
	}
	// Should NOT have "README.md, README.md"
	if strings.Contains(plain, "README.md, README.md") {
		t.Fatalf("duplicate file name not deduplicated in %q", plain)
	}
}

func TestExplorationGroupTruncatesLongCoalescedArgs(t *testing.T) {
	transcript := NewTranscript()
	longPath := strings.Repeat("deeply-nested-file-name-", 6) + ".go"

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-long",
		Args:       map[string]any{"path": "/workspace/" + longPath},
		Activity:   explorationActivity("read", map[string]any{"short_path": longPath}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-long",
		Result:     "content",
		Activity:   explorationActivity("read", map[string]any{"short_path": longPath}),
	})

	plain := transcriptPlain(transcript)
	if strings.Contains(plain, longPath) {
		t.Fatalf("exploration group kept unbounded args in %q", plain)
	}
	if !strings.Contains(plain, "Read deeply-nested-file-name-") || !strings.Contains(plain, "...") {
		t.Fatalf("exploration group missing truncated args in %q", plain)
	}
}

func TestExplorationGroupShowsExploringWhileInFlight(t *testing.T) {
	transcript := NewTranscript()

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-1",
		Args:       map[string]any{"path": "/workspace/README.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "README.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "grep",
		ToolCallID: "grep-1",
		Args:       map[string]any{"pattern": "RetryPromptPart", "path": "/workspace/src"},
		Activity:   explorationActivity("grep", map[string]any{"pattern": "RetryPromptPart", "short_path": "src"}),
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"● Read/Searched (2)",
		"  └ Read README.md",
		"    Search RetryPromptPart in src",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("live exploration group missing %q in %q", want, plain)
		}
	}
}

func TestExplorationGroupTruncatesHeadAndTail(t *testing.T) {
	transcript := NewTranscript()

	events := []struct {
		id       string
		toolName string
		args     map[string]any
		details  map[string]any
	}{
		{"read-1", "read", map[string]any{"path": "/workspace/session.py"}, map[string]any{"short_path": "session.py"}},
		{"grep-1", "grep", map[string]any{"pattern": "InvalidSession", "path": "/workspace/src"}, map[string]any{"pattern": "InvalidSession", "short_path": "src"}},
		{"ls-1", "ls", map[string]any{"path": "/workspace/tests"}, map[string]any{"short_path": "tests"}},
		{"find-1", "find", map[string]any{"pattern": "*.go", "path": "/workspace/internal"}, map[string]any{"pattern": "*.go", "short_path": "internal"}},
		{"grep-2", "grep", map[string]any{"pattern": "output validation", "path": "/workspace/tests"}, map[string]any{"pattern": "output validation", "short_path": "tests"}},
		{"ls-2", "ls", map[string]any{"path": "/workspace/docs"}, map[string]any{"short_path": "docs"}},
		{"find-2", "find", map[string]any{"pattern": "*.md", "path": "/workspace/docs"}, map[string]any{"pattern": "*.md", "short_path": "docs"}},
		{"read-2", "read", map[string]any{"path": "/workspace/jsonl.py"}, map[string]any{"short_path": "jsonl.py"}},
	}

	for _, event := range events {
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_started",
			ToolName:   event.toolName,
			ToolCallID: event.id,
			Args:       event.args,
			Activity:   explorationActivity(event.toolName, event.details),
		})
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_succeeded",
			ToolName:   event.toolName,
			ToolCallID: event.id,
			Result:     "done",
			Activity:   explorationActivity(event.toolName, event.details),
		})
	}

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"● Read/Searched (8) ok",
		"  └ Read session.py",
		"    Search InvalidSession in src",
		"    List tests",
		"    ... +3 more",
		"    Find *.md in docs",
		"    Read jsonl.py",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("truncated exploration group missing %q in %q", want, plain)
		}
	}
	for _, absent := range []string{
		"Find *.go in internal",
		"Search output validation in tests",
		"List docs",
	} {
		if strings.Contains(plain, absent) {
			t.Fatalf("truncated exploration group kept omitted line %q in %q", absent, plain)
		}
	}
}

func TestExplorationOperationalMissStaysGrouped(t *testing.T) {
	transcript := NewTranscript()
	duration := 15

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-miss",
		Args:       map[string]any{"path": "/workspace/agents.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "agents.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-miss",
		Result: map[string]any{
			"ok":      false,
			"message": "No such file or directory: '/workspace/agents.md'",
		},
		Activity: &rpc.ToolActivity{
			GroupKind:  explorationGroupKindPtr(),
			DurationMS: &duration,
			Summary:    strPtr("No such file or directory: '/workspace/agents.md'"),
			Details:    map[string]any{"short_path": "agents.md"},
		},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-hit",
		Args:       map[string]any{"path": "/workspace/AGENTS.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-hit",
		Result:     map[string]any{"output": "# Repository Guidelines"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"● Read/Searched (2) partial 15ms",
		"  └ Read agents.md  No such file or directory: '/workspace/agents.md'",
		"    Read AGENTS.md",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("operational miss grouped block missing %q in %q", want, plain)
		}
	}
}

func TestExplorationHardErrorStaysGrouped(t *testing.T) {
	transcript := NewTranscript()

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-1",
		Args:       map[string]any{"path": "/workspace/run.py"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "run.py"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-1",
		Result:     "read run.py",
		Activity:   explorationActivity("read", map[string]any{"short_path": "run.py"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "grep",
		ToolCallID: "grep-1",
		Args:       map[string]any{"pattern": "RetryPromptPart", "path": "/workspace/src"},
		Activity:   explorationActivity("grep", map[string]any{"pattern": "RetryPromptPart", "short_path": "src"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_failed",
		ToolName:   "grep",
		ToolCallID: "grep-1",
		Message:    "ripgrep (rg) is not installed",
		Activity:   explorationActivity("grep", map[string]any{"pattern": "RetryPromptPart", "short_path": "src"}),
	})

	plain := transcriptPlain(transcript)
	for _, want := range []string{
		"× Read/Searched (2) error",
		"  └ Read run.py",
		"    Search RetryPromptPart in src  error  ripgrep (rg) is not installed",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("hard error grouped block missing %q in %q", want, plain)
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

	transcript.replaceBlock(1, &rawCell{
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

	ac := transcript.blocks[0].(*assistantCell)
	if ac.cachedRender == "" {
		t.Fatal("completed assistant block rendered cache unexpectedly empty before render")
	}

	rendered := transcript.Render()
	if !strings.Contains(stripANSI(rendered), "This is a long completed assistant message.") {
		t.Fatalf("Render() missing completed assistant text: %q", rendered)
	}
	if ac.cachedRender != "" {
		t.Fatalf("completed assistant block kept rendered cache after render: %q", ac.cachedRender)
	}

	transcript.WriteLine("tail")
	rendered = transcript.Render()
	plainRendered := stripANSI(rendered)
	if !strings.Contains(plainRendered, "This is a long completed assistant message.") || !strings.Contains(plainRendered, "tail") {
		t.Fatalf("Render() after append lost cached transcript prefix: %q", plainRendered)
	}
	if ac.cachedRender != "" {
		t.Fatalf("completed assistant block rendered cache should stay evicted after append: %q", ac.cachedRender)
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

	plain := transcriptPlain(transcript)
	if strings.Contains(plain, longLine) {
		t.Fatalf("tool row kept unbounded long output line: %q", plain)
	}
	if !strings.Contains(plain, strings.Repeat("x", 32)+"...") {
		t.Fatalf("tool row missing truncated preview marker: %q", plain)
	}
}

func TestToolResultTruncationKeepsHeadAndTail(t *testing.T) {
	// 10 lines exceeds maxToolResultLines (6), so should be truncated
	// to 3 head + 2 tail with an ellipsis in between.
	lines := []string{
		"line-1", "line-2", "line-3", "line-4", "line-5",
		"line-6", "line-7", "line-8", "line-9", "line-10",
	}
	output := strings.Join(lines, "\n")

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-trunc",
		Args:       map[string]any{"command": "seq 10"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-trunc",
		Result:     map[string]any{"output": output},
	})

	plain := transcriptPlain(transcript)

	// Head lines must be present.
	for _, want := range []string{"line-1", "line-2", "line-3"} {
		if !strings.Contains(plain, want) {
			t.Fatalf("head line %q missing from truncated output: %q", want, plain)
		}
	}
	// Tail lines must be present.
	for _, want := range []string{"line-9", "line-10"} {
		if !strings.Contains(plain, want) {
			t.Fatalf("tail line %q missing from truncated output: %q", want, plain)
		}
	}
	// Middle lines must NOT be present.
	for _, absent := range []string{"line-4", "line-5", "line-6", "line-7", "line-8"} {
		if strings.Contains(plain, absent) {
			t.Fatalf("middle line %q should be omitted from truncated output: %q", absent, plain)
		}
	}
	// Ellipsis with omitted count must appear between head and tail.
	if !strings.Contains(plain, "... +5 more lines") {
		t.Fatalf("truncated output missing ellipsis with count: %q", plain)
	}

	// Verify ordering: head lines, then ellipsis, then tail lines.
	idxHead3 := strings.Index(plain, "line-3")
	idxEllipsis := strings.Index(plain, "... +5 more lines")
	idxTail9 := strings.Index(plain, "line-9")
	if idxHead3 >= idxEllipsis || idxEllipsis >= idxTail9 {
		t.Fatalf("unexpected ordering: head3@%d ellipsis@%d tail9@%d in %q",
			idxHead3, idxEllipsis, idxTail9, plain)
	}
}

func TestToolResultNoTruncationWhenWithinLimit(t *testing.T) {
	lines := []string{"alpha", "bravo", "charlie", "delta", "echo", "foxtrot"}
	output := strings.Join(lines, "\n")

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-short",
		Args:       map[string]any{"command": "echo"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "shell",
		ToolCallID: "call-short",
		Result:     map[string]any{"output": output},
	})

	plain := transcriptPlain(transcript)
	for _, want := range lines {
		if !strings.Contains(plain, want) {
			t.Fatalf("line %q missing from non-truncated output: %q", want, plain)
		}
	}
	if strings.Contains(plain, "...") {
		t.Fatalf("non-truncated output should not contain ellipsis: %q", plain)
	}
}

func TestTruncateLinesUnitHeadTail(t *testing.T) {
	input := []string{"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"}
	result, truncated, omitted, headCount := truncateLines(input, 6)
	if !truncated {
		t.Fatal("expected truncated=true")
	}
	if omitted != 5 {
		t.Fatalf("omitted = %d, want 5", omitted)
	}
	if headCount != 3 {
		t.Fatalf("headCount = %d, want 3", headCount)
	}
	if len(result) != 5 {
		t.Fatalf("len(result) = %d, want 5 (3 head + 2 tail)", len(result))
	}
	// First 3 are head.
	for i, want := range []string{"a", "b", "c"} {
		if result[i] != want {
			t.Fatalf("result[%d] = %q, want %q", i, result[i], want)
		}
	}
	// Last 2 are tail.
	for i, want := range []string{"i", "j"} {
		if result[3+i] != want {
			t.Fatalf("result[%d] = %q, want %q", 3+i, result[3+i], want)
		}
	}
}

func TestTruncateLinesNoTruncation(t *testing.T) {
	input := []string{"a", "b", "c"}
	result, truncated, omitted, headCount := truncateLines(input, 6)
	if truncated {
		t.Fatal("expected truncated=false")
	}
	if omitted != 0 || headCount != 0 {
		t.Fatalf("omitted=%d headCount=%d, want 0, 0", omitted, headCount)
	}
	if len(result) != 3 {
		t.Fatalf("len(result) = %d, want 3", len(result))
	}
}

func TestCodeBlockLanguageLabelRendered(t *testing.T) {
	markdown := "Here is code:\n\n```python\nprint('hello')\n```\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "│ python") {
		t.Fatalf("rendered code block missing language label: %q", plain)
	}
	if !strings.Contains(plain, "│ print('hello')") {
		t.Fatalf("rendered code block missing code content: %q", plain)
	}
}

func TestRunSucceededRendersBackendSeparatorSummary(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteUserTurn("check the repo")
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "run_succeeded",
		RunID:      "run-1",
		OutputText: "done",
		TranscriptSummary: &rpc.RunTranscriptSummary{
			ElapsedMS:           179000,
			ToolCallCount:       5,
			ToolDurationMS:      1234,
			TotalTokens:         intPtr(82000),
			ContextWindowUsed:   floatPtr(0.41),
			HadWorkActivity:     true,
			ShouldShowSeparator: true,
			ActivityGroups: []rpc.ActivityGroupSummary{
				{
					GroupKind:  "execution",
					GroupLabel: "Shell",
					GroupCounts: rpc.ActivityGroupCounts{
						Shell: 5,
						Tool:  5,
					},
					Outcome:   "success",
					ElapsedMS: intPtr(1234),
				},
			},
		},
	})

	plain := stripANSI(transcript.Render())
	if !strings.Contains(plain, "-- jaca ") || !strings.Contains(plain, " for 2m 59s --") {
		t.Fatalf("separator missing curated verb line in %q", plain)
	}
	for _, verb := range runSeparatorVerbs {
		needle := "-- jaca " + verb + " for 2m 59s --"
		if strings.Contains(plain, needle) {
			goto foundSeparatorVerb
		}
	}
	t.Fatalf("separator did not use curated verb in %q", plain)

foundSeparatorVerb:
	for _, absent := range []string{
		"Shell",
		"tools 1.2s",
		"82k tok",
		"41% ctx",
	} {
		if strings.Contains(plain, absent) {
			t.Fatalf("separator should not include %q in %q", absent, plain)
		}
	}
}

func TestRunSucceededOmitsSeparatorWhenBackendSaysNo(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteUserTurn("short run")
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "run_succeeded",
		RunID:      "run-1",
		OutputText: "done",
		TranscriptSummary: &rpc.RunTranscriptSummary{
			ElapsedMS:           400,
			ToolCallCount:       1,
			ToolDurationMS:      40,
			HadWorkActivity:     true,
			ShouldShowSeparator: false,
		},
	})

	plain := stripANSI(transcript.Render())
	if strings.Contains(plain, "jaca run") {
		t.Fatalf("separator rendered despite backend false hint: %q", plain)
	}
}

func TestBlockquoteRendered(t *testing.T) {
	markdown := "> This is a quote\n>> Nested quote\n"

	transcript := NewTranscript()
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: markdown})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: markdown})

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
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

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
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

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
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

	rendered := transcript.blocks[len(transcript.blocks)-1].Render()
	plain := stripANSI(rendered)
	if !strings.Contains(plain, "│ plain code") {
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

func TestWrapLinesPreservesHyperlinks(t *testing.T) {
	input := renderHyperlink("https://auth.example.test/login", "Open login link (auth.example.test)") + " for browser login completion"
	got := wrapLines(input, 28)
	if !strings.Contains(got, "\x1b]8;;https://auth.example.test/login\x1b\\") {
		t.Fatalf("wrapLines stripped OSC 8 hyperlink: %q", got)
	}
	for i, line := range strings.Split(got, "\n") {
		if visibleLen(line) > 28 {
			t.Fatalf("line %d exceeds width 28: %q (visible=%d)", i, line, visibleLen(line))
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

func TestStreamCollectorCommitsOnNewline(t *testing.T) {
	sc := &StreamCollector{}

	// Push text without newline — nothing should be committed.
	sc.PushDelta("## Hello")
	result := sc.CommitCompleteLines()
	if result != "" {
		t.Fatalf("expected empty rendered before any newline, got %q", result)
	}

	// Push a newline — the complete line should now be markdown-rendered.
	sc.PushDelta("\n")
	result = sc.CommitCompleteLines()
	if result == "" {
		t.Fatal("expected non-empty rendered after newline")
	}
	plain := stripANSI(result)
	if !strings.Contains(plain, "Hello") {
		t.Fatalf("committed output missing heading text: %q", plain)
	}

	// Push more text with embedded newline.
	sc.PushDelta("**bold text**\npartial")
	result = sc.CommitCompleteLines()
	plain = stripANSI(result)
	if !strings.Contains(plain, "bold text") {
		t.Fatalf("committed output missing bold text: %q", plain)
	}
	// "partial" has no trailing newline, so it should NOT appear in committed output.
	// But note: the committed portion includes everything up to last \n, which is
	// "## Hello\n**bold text**\n". The partial "partial" is uncommitted.
	tail := sc.PartialTail()
	if tail != "partial" {
		t.Fatalf("expected partial tail 'partial', got %q", tail)
	}
}

func TestStreamCollectorFinalizeDrainsPartial(t *testing.T) {
	sc := &StreamCollector{}
	sc.PushDelta("some **partial** line")

	// CommitCompleteLines should return nothing (no newline).
	result := sc.CommitCompleteLines()
	if result != "" {
		t.Fatalf("expected empty before finalize, got %q", result)
	}

	// FinalizeAndDrain should render the partial line.
	result = sc.FinalizeAndDrain()
	if result == "" {
		t.Fatal("FinalizeAndDrain returned empty")
	}
	plain := stripANSI(result)
	if !strings.Contains(plain, "partial") {
		t.Fatalf("finalized output missing content: %q", plain)
	}

	// After finalize, state should be reset.
	if sc.PlainText() != "" {
		t.Fatalf("buffer not reset after FinalizeAndDrain: %q", sc.PlainText())
	}
}

func TestStreamingAssistantShowsMarkdown(t *testing.T) {
	original := lipgloss.ColorProfile()
	t.Cleanup(func() {
		lipgloss.SetColorProfile(original)
	})
	lipgloss.SetColorProfile(termenv.TrueColor)

	transcript := NewTranscript()

	// Simulate streaming deltas that include markdown formatting.
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "## Title\n"})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "**bold** and "})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "`code`\n"})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "partial tail"})

	// During streaming (before run_succeeded), the live block should contain
	// markdown-rendered content for committed lines.
	liveBlock := transcript.blocks[transcript.liveAssistantIdx]
	rendered := liveBlock.Render()
	plain := stripANSI(rendered)

	// The heading "Title" should be present (rendered from committed line).
	if !strings.Contains(plain, "Title") {
		t.Fatalf("streaming rendered missing heading: %q", plain)
	}

	// "bold" and "code" should be present (from committed second line).
	if !strings.Contains(plain, "bold") {
		t.Fatalf("streaming rendered missing bold text: %q", plain)
	}
	if !strings.Contains(plain, "code") {
		t.Fatalf("streaming rendered missing code text: %q", plain)
	}

	// The partial tail should also be present (as raw text).
	if !strings.Contains(plain, "partial tail") {
		t.Fatalf("streaming rendered missing partial tail: %q", plain)
	}

	// The markdown markers (## and **) should be stripped, proving markdown
	// processing happened during streaming, not just raw text display.
	if strings.Contains(plain, "##") {
		t.Fatalf("streaming rendered still contains raw heading markers: %q", plain)
	}
	if strings.Contains(plain, "**") {
		t.Fatalf("streaming rendered still contains raw bold markers: %q", plain)
	}

	// The rendered output should contain ANSI formatting codes.
	if !strings.Contains(rendered, "\x1b[") {
		t.Fatalf("streaming rendered has no ANSI codes, markdown not processed: %q", rendered)
	}

	// The "● " marker should appear exactly once.
	markerCount := strings.Count(plain, "●")
	if markerCount != 1 {
		t.Fatalf("expected exactly 1 marker, got %d in: %q", markerCount, plain)
	}
}

func TestTranscriptKeepsOnlyRecentCompletedRuns(t *testing.T) {
	transcript := NewTranscript()
	transcript.WriteStartupBanner("0.1.0", "openai:gpt-5.4", "/workspace", "")

	for i := 1; i <= 12; i++ {
		transcript.WriteUserTurn(fmt.Sprintf("prompt %02d", i))
		transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: fmt.Sprintf("answer %02d", i)})
	}

	rendered := stripANSI(transcript.Render())
	if !strings.Contains(rendered, "older completed runs omitted (2)") {
		t.Fatalf("missing omission marker in %q", rendered)
	}
	for _, absent := range []string{"prompt 01", "answer 01", "prompt 02", "answer 02"} {
		if strings.Contains(rendered, absent) {
			t.Fatalf("old run content %q should be omitted from %q", absent, rendered)
		}
	}
	for _, want := range []string{"prompt 03", "answer 03", "prompt 12", "answer 12"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("recent run content %q missing from %q", want, rendered)
		}
	}
}

func TestTranscriptPrunesOldToolRowsWithCompletedRuns(t *testing.T) {
	transcript := NewTranscript()

	for i := 1; i <= 11; i++ {
		transcript.WriteUserTurn(fmt.Sprintf("prompt %02d", i))
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_started",
			ToolName:   "shell",
			ToolCallID: fmt.Sprintf("call-%02d", i),
			Args:       map[string]any{"command": fmt.Sprintf("echo run-%02d", i)},
		})
		transcript.ApplyRunEvent(rpc.RunEvent{
			Type:       "tool_call_succeeded",
			ToolName:   "shell",
			ToolCallID: fmt.Sprintf("call-%02d", i),
			Result:     map[string]any{"output": fmt.Sprintf("run-%02d", i)},
		})
		transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: fmt.Sprintf("answer %02d", i)})
	}

	rendered := stripANSI(transcript.Render())
	if strings.Contains(rendered, "echo run-01") {
		t.Fatalf("old tool row should be omitted from %q", rendered)
	}
	if !strings.Contains(rendered, "echo run-11") {
		t.Fatalf("recent tool row missing from %q", rendered)
	}
}

func TestTranscriptKeepsCurrentLiveRunVisibleBeyondCompletedRunLimit(t *testing.T) {
	transcript := NewTranscript()

	for i := 1; i <= 10; i++ {
		transcript.WriteUserTurn(fmt.Sprintf("prompt %02d", i))
		transcript.ApplyRunEvent(rpc.RunEvent{Type: "run_succeeded", OutputText: fmt.Sprintf("answer %02d", i)})
	}

	transcript.WriteUserTurn("prompt 11")
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "shell",
		ToolCallID: "call-live",
		Args:       map[string]any{"command": "echo live"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{Type: "assistant_text_delta", Delta: "working"})

	rendered := stripANSI(transcript.Render())
	if strings.Contains(rendered, "older completed runs omitted") {
		t.Fatalf("current live run should not trigger pruning before completion: %q", rendered)
	}
	for _, want := range []string{"prompt 01", "answer 01", "prompt 10", "answer 10", "prompt 11", "echo live", "working"} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("visible transcript missing %q in %q", want, rendered)
		}
	}
}

func TestToolPhaseChangeSplitsExplorationAndEditingIntoSeparateBlocks(t *testing.T) {
	transcript := NewTranscript()

	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-1",
		Args:       map[string]any{"path": "AGENTS.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-1",
		Result:     "content",
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "edit",
		ToolCallID: "edit-1",
		Args:       map[string]any{"path": "AGENTS.md"},
		Activity:   &rpc.ToolActivity{Title: "edit AGENTS.md"},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "edit",
		ToolCallID: "edit-1",
		Result:     "edited AGENTS.md",
		Activity: &rpc.ToolActivity{
			Title:      "edit AGENTS.md",
			Summary:    strPtr("edit applied"),
			DurationMS: intPtr(4),
			Details: map[string]any{
				"kind": "edit",
				"path": "AGENTS.md",
				"diff": "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-old\n+new\n",
			},
		},
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_started",
		ToolName:   "read",
		ToolCallID: "read-2",
		Args:       map[string]any{"path": "AGENTS.md"},
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})
	transcript.ApplyRunEvent(rpc.RunEvent{
		Type:       "tool_call_succeeded",
		ToolName:   "read",
		ToolCallID: "read-2",
		Result:     "content",
		Activity:   explorationActivity("read", map[string]any{"short_path": "AGENTS.md"}),
	})

	rendered := transcript.Render()
	plain := stripANSI(rendered)
	for _, want := range []string{
		"● Read/Searched (1) ok",
		"  └ Read AGENTS.md",
		"● edit  AGENTS.md  ok  4ms",
		"● Read/Searched (1) ok",
	} {
		if !strings.Contains(plain, want) {
			t.Fatalf("phase-split transcript missing %q in %q", want, plain)
		}
	}
	if strings.Count(plain, "● Read/Searched (1) ok") != 2 {
		t.Fatalf("expected two separate read/searched blocks in %q", plain)
	}
}

func strPtr(value string) *string {
	return &value
}

func explorationGroupKindPtr() *string {
	value := "exploration"
	return &value
}

func explorationActivity(toolName string, details map[string]any) *rpc.ToolActivity {
	if details == nil {
		details = map[string]any{}
	}
	displayLabel := map[string]string{
		"read": "Read",
		"grep": "Search",
		"find": "Find",
		"ls":   "List",
	}[toolName]
	return &rpc.ToolActivity{
		DisplayLabel: &displayLabel,
		GroupKind:    explorationGroupKindPtr(),
		Details:      details,
	}
}
