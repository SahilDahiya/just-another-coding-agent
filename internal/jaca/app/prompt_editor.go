package app

import (
	"strings"

	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// clearTextareaCursorLineStyle wipes the default cursor-line background
// bubbles/textarea applies in focused state (lipgloss.AdaptiveColor Light:255
// Dark:0). That background makes the composer row look like a different
// shade than the rest of the prompt frame — jarring in a flat-theme TUI.
func clearTextareaCursorLineStyle(ta *textarea.Model) {
	blank := lipgloss.NewStyle()
	ta.FocusedStyle.CursorLine = blank
	ta.FocusedStyle.CursorLineNumber = blank
	ta.BlurredStyle.CursorLine = blank
	ta.BlurredStyle.CursorLineNumber = blank
}

// promptEditorVisibleMax caps how tall the composer is allowed to grow on
// screen before it starts scrolling internally. Picked to match typical
// terminal use without eating too much vertical space.
const promptEditorVisibleMax = 10

// promptEditorHardMaxLines is the hard ceiling on logical lines the editor
// will accept. This limit exists so the memoization cache textarea allocates
// (sized to MaxHeight) stays bounded; it should be large enough that a real
// user never hits it while drafting a prompt.
const promptEditorHardMaxLines = 500

// promptEditor is the JACA composer. It wraps bubbles/textarea so the call
// sites keep a textinput-shaped surface (Value / SetValue / CursorEnd /
// Focus / Blur / Update / View) while gaining multi-line support underneath.
//
// width is tracked here because textarea does not expose a public getter and
// the wrapper needs it to compute wrapped row counts for syncHeight.
type promptEditor struct {
	ta    textarea.Model
	width int
}

func newPromptEditor() promptEditor {
	ta := textarea.New()
	ta.Prompt = ""
	ta.Placeholder = ""
	ta.ShowLineNumbers = false
	ta.CharLimit = 0
	ta.MaxHeight = promptEditorHardMaxLines
	ta.Cursor.SetMode(cursor.CursorStatic)
	clearTextareaCursorLineStyle(&ta)
	ta.SetHeight(1)
	ta.SetWidth(80)
	ta.Focus()
	return promptEditor{ta: ta, width: 80}
}

func (e promptEditor) Value() string {
	return e.ta.Value()
}

func (e *promptEditor) SetValue(s string) {
	e.ta.SetValue(s)
	e.syncHeight()
}

func (e *promptEditor) CursorEnd() {
	e.ta.CursorEnd()
}

func (e *promptEditor) Focus() tea.Cmd {
	return e.ta.Focus()
}

func (e *promptEditor) Blur() {
	e.ta.Blur()
}

func (e *promptEditor) SetWidth(w int) {
	if w < 1 {
		w = 1
	}
	e.width = w
	e.ta.SetWidth(w)
	e.syncHeight()
}

func (e promptEditor) View() string {
	return e.ta.View()
}

// InsertNewline inserts a literal newline at the cursor. Used by the
// Enter-vs-newline policy layered in handleKey.
func (e *promptEditor) InsertNewline() {
	e.ta.InsertRune('\n')
	e.syncHeight()
}

// InsertString inserts s at the cursor. Used by the large-paste placeholder
// path to put a compact label in the composer instead of the raw paste.
func (e *promptEditor) InsertString(s string) {
	e.ta.InsertString(s)
	e.syncHeight()
}

func (e promptEditor) Update(msg tea.Msg) (promptEditor, tea.Cmd) {
	ta, cmd := e.ta.Update(msg)
	e.ta = ta
	e.syncHeight()
	return e, cmd
}

// AtFirstVisualRow reports whether the cursor sits on the topmost visual row
// of the draft (first wrapped row of the first logical line). handleKey uses
// this to decide whether `up` should navigate prompt history or move within
// the textarea.
func (e promptEditor) AtFirstVisualRow() bool {
	if e.ta.Line() != 0 {
		return false
	}
	return e.ta.LineInfo().RowOffset == 0
}

// AtLastVisualRow reports whether the cursor sits on the bottom visual row of
// the draft (last wrapped row of the last logical line). Counterpart to
// AtFirstVisualRow for the `down` key.
func (e promptEditor) AtLastVisualRow() bool {
	if e.ta.Line() != e.ta.LineCount()-1 {
		return false
	}
	li := e.ta.LineInfo()
	return li.RowOffset+1 >= li.Height
}

// syncHeight keeps the visible area just tall enough to hold the content —
// including soft-wrapped rows — so a long line that wraps does not force the
// user to type blind past the edge of the composer. Capped at
// promptEditorVisibleMax; beyond that the textarea scrolls internally.
func (e *promptEditor) syncHeight() {
	width := e.width
	if width < 1 {
		width = 1
	}
	rows := 0
	for _, line := range strings.Split(e.ta.Value(), "\n") {
		w := lipgloss.Width(line)
		// +1 leaves room for the cursor column textarea reserves at EOL.
		perLine := (w + 1 + width - 1) / width
		if perLine < 1 {
			perLine = 1
		}
		rows += perLine
	}
	if rows < 1 {
		rows = 1
	}
	if rows > promptEditorVisibleMax {
		rows = promptEditorVisibleMax
	}
	e.ta.SetHeight(rows)
}
