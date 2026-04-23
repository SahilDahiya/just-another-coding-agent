package app

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// countVisibleRows returns how many rendered rows the editor's current view
// occupies, which is what drives whether the user can see their whole draft.
func countVisibleRows(e promptEditor) int {
	return strings.Count(e.View(), "\n") + 1
}

func TestPromptEditorGrowsForWrappedSingleLine(t *testing.T) {
	e := newPromptEditor()
	e.SetWidth(20)
	// One logical line long enough to wrap onto multiple visual rows.
	e.SetValue(strings.Repeat("a", 65))
	if rows := countVisibleRows(e); rows < 3 {
		t.Fatalf("expected >=3 visible rows for a wrapped 65-char line at width 20, got %d", rows)
	}
}

func TestPromptEditorKeepsSingleRowForShortLine(t *testing.T) {
	e := newPromptEditor()
	e.SetWidth(80)
	e.SetValue("short")
	if rows := countVisibleRows(e); rows != 1 {
		t.Fatalf("expected 1 visible row for a short single-line draft, got %d", rows)
	}
}

func TestPromptEditorGrowsForExplicitNewlines(t *testing.T) {
	e := newPromptEditor()
	e.SetWidth(80)
	e.SetValue("line1\nline2\nline3")
	if rows := countVisibleRows(e); rows != 3 {
		t.Fatalf("expected 3 visible rows for 3 logical lines, got %d", rows)
	}
}

func TestNewlineKeybindingsInsertNewline(t *testing.T) {
	cases := []struct {
		name string
		msg  tea.KeyMsg
	}{
		{"alt+enter", tea.KeyMsg{Type: tea.KeyEnter, Alt: true}},
		{"ctrl+j", tea.KeyMsg{Type: tea.KeyCtrlJ}},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			m := newTestModel()
			m = sendRunes(m, "hi")
			updated, _ := m.Update(tc.msg)
			m = updated.(*model)
			if !strings.Contains(m.textInput.Value(), "\n") {
				t.Fatalf("%s should have inserted a newline, value=%q", tc.name, m.textInput.Value())
			}
		})
	}
}

// Regression: a draft ending in a literal backslash must submit verbatim.
// An earlier `\`+Enter newline-fallback silently rewrote `C:\repo\` to
// `C:\repo\n`, which then trimmed to `C:\repo` on the real submit —
// corrupting user input.
func TestTrailingBackslashSubmitsLiterally(t *testing.T) {
	m := newTestModel()
	m.textInput.SetValue(`C:\repo\`)
	prompt := m.consumePromptDraft()
	if prompt != `C:\repo\` {
		t.Fatalf("trailing backslash should survive submit, got %q", prompt)
	}
}

// Regression: the visible-height cap must not bound how many logical newlines
// the user can insert. textarea.InsertNewline silently drops Enter once
// len(value) >= MaxHeight, so the two must stay decoupled.
func TestNewlineInsertionExceedsVisibleCap(t *testing.T) {
	e := newPromptEditor()
	e.SetWidth(80)
	target := promptEditorVisibleMax + 15
	for i := 0; i < target-1; i++ {
		e.InsertNewline()
	}
	got := strings.Count(e.Value(), "\n") + 1
	if got != target {
		t.Fatalf("expected %d logical lines after inserting past visible cap, got %d", target, got)
	}
}

func TestUpInsideMultilineDraftMovesCursorNotHistory(t *testing.T) {
	m := newTestModel()
	// Seed history so historyPrevious would do something visible.
	m.promptHistory = []string{"old prompt"}
	m.historyIndex = -1

	m.textInput.SetValue("line1\nline2")
	// Cursor lands at end (line2). Pressing up inside the draft should move
	// to line1, not replace the draft with "old prompt".
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyUp})
	if m.textInput.Value() != "line1\nline2" {
		t.Fatalf("draft was overwritten by history navigation: %q", m.textInput.Value())
	}
}

func TestUpAtFirstRowNavigatesHistory(t *testing.T) {
	m := newTestModel()
	m.promptHistory = []string{"old prompt"}
	m.historyIndex = -1

	m.textInput.SetValue("only line")
	m = sendKey(m, tea.KeyMsg{Type: tea.KeyUp})
	if m.textInput.Value() != "old prompt" {
		t.Fatalf("history should have replaced the single-line draft, got %q", m.textInput.Value())
	}
}
