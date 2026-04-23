package app

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestPasteStoreRegisterAndExpand(t *testing.T) {
	s := newPasteStore()
	body := strings.Repeat("x", 2000)
	label := s.Register(body)
	if !strings.HasPrefix(label, "[pasted 2000 chars") {
		t.Fatalf("unexpected label %q", label)
	}
	draft := "prefix " + label + " suffix"
	if got := s.Expand(draft); got != "prefix "+body+" suffix" {
		t.Fatalf("expand returned unexpected length=%d", len(got))
	}
}

func TestPasteStoreExpandLongestLabelFirst(t *testing.T) {
	s := newPasteStore()
	short := s.Register(strings.Repeat("a", pasteThreshold))
	long := s.Register(strings.Repeat("b", pasteThreshold*2))
	draft := short + "|" + long
	got := s.Expand(draft)
	if !strings.Contains(got, strings.Repeat("a", pasteThreshold)) || !strings.Contains(got, strings.Repeat("b", pasteThreshold*2)) {
		t.Fatalf("expanded text missing one of the bodies")
	}
}

func TestPasteStorePruneMissing(t *testing.T) {
	s := newPasteStore()
	label := s.Register(strings.Repeat("z", pasteThreshold))
	s.PruneMissing("no label here")
	if len(s.entries) != 0 {
		t.Fatalf("expected entries pruned, got %d", len(s.entries))
	}
	if got := s.Expand(label); got != label {
		t.Fatalf("expand after prune should be literal, got %q", got)
	}
}

func TestLargePasteInsertsPlaceholder(t *testing.T) {
	m := newTestModel()
	body := strings.Repeat("x", pasteThreshold+500)
	msg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(msg)
	m = updated.(*model)

	value := m.textInput.Value()
	if strings.Contains(value, body) {
		t.Fatalf("composer should contain label, not raw body")
	}
	if !strings.HasPrefix(value, "[pasted ") {
		t.Fatalf("composer value should start with placeholder label, got %q", value)
	}
	if got := m.consumePromptDraft(); got != body {
		t.Fatalf("expanded draft should match raw body (got len %d, want %d)", len(got), len(body))
	}
}

func TestSmallPasteInsertedLiterally(t *testing.T) {
	m := newTestModel()
	body := "short paste stays inline"
	msg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(msg)
	m = updated.(*model)

	if got := m.textInput.Value(); got != body {
		t.Fatalf("small paste should appear literally, got %q", got)
	}
	if len(m.pastes.entries) != 0 {
		t.Fatalf("small paste must not register an entry")
	}
}

func TestConsumePromptDraftExpandsPlaceholders(t *testing.T) {
	// The model's submit paths all read the prompt through consumePromptDraft.
	// This test isolates that seam: a pasted placeholder still expands back
	// to the raw body when the backend fetches the prompt for submission.
	m := newTestModel()
	body := strings.Repeat("p", pasteThreshold+200)
	msg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(msg)
	m = updated.(*model)

	if got := m.consumePromptDraft(); got != body {
		t.Fatalf("consumePromptDraft should return expanded body, got len %d want %d", len(got), len(body))
	}
}

// Regression: when an OAuth / credential detour interrupts a submit, the
// stashed PendingPrompt must keep the paste placeholder label so that after
// the detour finishes the composer still looks scannable. The pasteStore is
// untouched across the detour, so re-submission expands correctly.
func TestOAuthDetourPreservesPastePlaceholder(t *testing.T) {
	m := newTestModelWithBackend(newStubBackend())
	m.options.Model = "openai-codex:gpt-5-codex"

	body := strings.Repeat("z", pasteThreshold+300)
	pasteMsg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(pasteMsg)
	m = updated.(*model)

	label := m.textInput.Value()
	if !strings.HasPrefix(label, "[pasted ") {
		t.Fatalf("expected label in composer before submit, got %q", label)
	}

	// Force the detour: call startOpenAICodexLoginFlow the way handleEnter
	// does after the logged-in check fails. The pending draft we hand it
	// must be the raw value, not the expanded body.
	pendingDraft := m.textInput.Value()
	updated, _ = m.startOpenAICodexLoginFlow(m.options.Model, pendingDraft)
	m = updated.(*model)

	if got := m.login.PendingPrompt; got != label {
		t.Fatalf("PendingPrompt should be the labelled draft, got %q", got)
	}
	if len(m.pastes.entries) != 1 {
		t.Fatalf("pasteStore must stay alive across detour, got %d entries", len(m.pastes.entries))
	}

	// Simulate detour ending: restore draft to composer.
	m.endLoginFlow()
	m.restorePendingPrompt(pendingDraft)
	if got := m.textInput.Value(); got != label {
		t.Fatalf("restored composer should still show label, got %q", got)
	}
	// And pressing Enter (via consumePromptDraft) expands back to full body.
	if got := m.consumePromptDraft(); got != body {
		t.Fatalf("consumePromptDraft after detour should expand to body, got len %d want %d", len(got), len(body))
	}
}

// Regression: while the user is typing an OAuth code or API secret into the
// composer during a detour, PruneMissing must not drop the stashed paste
// entry just because its label temporarily isn't in the composer.
func TestAuthLoginTypingDoesNotPruneStashedPaste(t *testing.T) {
	m := newTestModelWithBackend(newStubBackend())
	m.options.Model = "openai-codex:gpt-5-codex"

	body := strings.Repeat("w", pasteThreshold+200)
	pasteMsg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(pasteMsg)
	m = updated.(*model)

	label := m.textInput.Value()
	pendingDraft := label

	// Enter the OAuth detour. The composer is cleared so the user can type
	// the browser-provided completion code.
	updated, _ = m.startOpenAICodexLoginFlow(m.options.Model, pendingDraft)
	m = updated.(*model)
	m.login.Waiting = true
	m.login.FlowID = "flow-id"

	// Simulate the user typing the code. Each keystroke would normally run
	// through m.Update -> handleKey default branch -> PruneMissing.
	for _, r := range "abc-123" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(*model)
	}

	if len(m.pastes.entries) != 1 {
		t.Fatalf("paste entry was pruned while typing the OAuth code (entries=%d)", len(m.pastes.entries))
	}

	// After login completes, restore the labelled draft and expand.
	m.endLoginFlow()
	m.restorePendingPrompt(pendingDraft)
	if got := m.consumePromptDraft(); got != body {
		t.Fatalf("expected pasted body back after detour+restore, got len %d want %d", len(got), len(body))
	}
}

// Regression: ctrl+u clears the composer line but must not wipe the paste
// store during an OAuth detour. The stashed draft in PendingPrompt still
// needs its labels to expand after the detour finishes.
func TestCtrlUDuringOAuthPreservesStashedPaste(t *testing.T) {
	m := newTestModelWithBackend(newStubBackend())
	m.options.Model = "openai-codex:gpt-5-codex"

	body := strings.Repeat("m", pasteThreshold+400)
	pasteMsg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(pasteMsg)
	m = updated.(*model)

	pendingDraft := m.textInput.Value()
	updated, _ = m.startOpenAICodexLoginFlow(m.options.Model, pendingDraft)
	m = updated.(*model)
	m.login.Waiting = true
	m.login.FlowID = "flow-id"

	// User types a partial code then reaches for ctrl+u to retry.
	for _, r := range "abc" {
		updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(*model)
	}
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyCtrlU})
	m = updated.(*model)

	if len(m.pastes.entries) != 1 {
		t.Fatalf("ctrl+u during OAuth wiped the paste store (entries=%d)", len(m.pastes.entries))
	}
	if m.textInput.Value() != "" {
		t.Fatalf("ctrl+u should have cleared the composer line, got %q", m.textInput.Value())
	}

	// Finish the detour and prove the draft still expands to the full body.
	m.endLoginFlow()
	m.restorePendingPrompt(pendingDraft)
	if got := m.consumePromptDraft(); got != body {
		t.Fatalf("draft no longer expands to body after ctrl+u+detour, got len %d", len(got))
	}
}

func TestBackspacedPlaceholderDropsEntry(t *testing.T) {
	m := newTestModel()
	body := strings.Repeat("q", pasteThreshold+50)
	msg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(body), Paste: true}
	updated, _ := m.Update(msg)
	m = updated.(*model)

	if len(m.pastes.entries) != 1 {
		t.Fatalf("expected 1 entry after paste, got %d", len(m.pastes.entries))
	}

	m.textInput.SetValue("")
	m.pastes.PruneMissing(m.textInput.Value())
	if len(m.pastes.entries) != 0 {
		t.Fatalf("entry should be pruned when label leaves the draft")
	}
}
