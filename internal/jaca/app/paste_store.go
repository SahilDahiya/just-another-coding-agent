package app

import (
	"fmt"
	"sort"
	"strings"
)

// pasteThreshold is the minimum rune count that turns a paste into a
// placeholder. Below this, the paste is inserted literally so short pastes
// (URLs, one-line snippets) still look like normal input.
const pasteThreshold = 1000

// pasteEntry holds the real text behind a placeholder label shown in the
// composer. The label is the visible token, e.g. "[pasted 7624 chars]".
type pasteEntry struct {
	Label   string
	Content string
}

// pasteStore tracks placeholders that currently live in the composer draft.
// Entries are keyed by label; on submit we expand labels back to Content.
// On edit we prune labels that no longer appear in the draft so deleted
// placeholders don't silently leak into the next submission.
type pasteStore struct {
	next    int
	entries map[string]pasteEntry
}

func newPasteStore() pasteStore {
	return pasteStore{entries: map[string]pasteEntry{}}
}

// Register stores content and returns the label to insert into the composer.
// Labels are stable within a session so expand() can round-trip them.
func (s *pasteStore) Register(content string) string {
	s.next++
	label := fmt.Sprintf("[pasted %d chars #%d]", len([]rune(content)), s.next)
	if s.entries == nil {
		s.entries = map[string]pasteEntry{}
	}
	s.entries[label] = pasteEntry{Label: label, Content: content}
	return label
}

// Expand replaces every known label in draft with its stored content.
// Labels are matched longest-first so a label that's a substring of another
// never wins. Empty store is a fast-path no-op.
func (s *pasteStore) Expand(draft string) string {
	if len(s.entries) == 0 {
		return draft
	}
	labels := make([]string, 0, len(s.entries))
	for label := range s.entries {
		labels = append(labels, label)
	}
	sort.Slice(labels, func(i, j int) bool { return len(labels[i]) > len(labels[j]) })
	for _, label := range labels {
		entry := s.entries[label]
		draft = strings.ReplaceAll(draft, label, entry.Content)
	}
	return draft
}

// PruneMissing drops entries whose labels are no longer present in draft.
// Called after each edit so that a user backspacing through a placeholder
// cleanly removes the stored content too.
func (s *pasteStore) PruneMissing(draft string) {
	for label := range s.entries {
		if !strings.Contains(draft, label) {
			delete(s.entries, label)
		}
	}
}

// Reset clears all entries. Called after submit so the next draft starts
// with a clean slate.
func (s *pasteStore) Reset() {
	s.entries = map[string]pasteEntry{}
	s.next = 0
}
