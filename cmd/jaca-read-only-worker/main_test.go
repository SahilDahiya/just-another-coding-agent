package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSplitLinesKeepEnds(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected []string
	}{
		{"empty", "", nil},
		{"single line no newline", "hello", []string{"hello"}},
		{"single line with newline", "hello\n", []string{"hello\n"}},
		{"two lines", "a\nb\n", []string{"a\n", "b\n"}},
		{"trailing content no newline", "a\nb", []string{"a\n", "b"}},
		{"blank lines", "\n\n\n", []string{"\n", "\n", "\n"}},
		{"mixed", "line1\n\nline3\n", []string{"line1\n", "\n", "line3\n"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := splitLinesKeepEnds(tt.input)
			if len(result) != len(tt.expected) {
				t.Fatalf("got %d lines, want %d: %q", len(result), len(tt.expected), result)
			}
			for i, line := range result {
				if line != tt.expected[i] {
					t.Errorf("line %d: got %q, want %q", i, line, tt.expected[i])
				}
			}
		})
	}
}

func TestTruncateHeadLineWindow(t *testing.T) {
	lines := []string{"aaa\n", "bbb\n", "ccc\n", "ddd\n"}

	t.Run("all lines fit", func(t *testing.T) {
		text, count, truncated, firstTooLarge := truncateHeadLineWindow(lines, 10, 100)
		if text != "aaa\nbbb\nccc\nddd\n" || count != 4 || truncated || firstTooLarge {
			t.Errorf("unexpected: text=%q count=%d truncated=%v firstTooLarge=%v", text, count, truncated, firstTooLarge)
		}
	})

	t.Run("line limit", func(t *testing.T) {
		text, count, truncated, firstTooLarge := truncateHeadLineWindow(lines, 2, 100)
		if text != "aaa\nbbb\n" || count != 2 || !truncated || firstTooLarge {
			t.Errorf("unexpected: text=%q count=%d truncated=%v firstTooLarge=%v", text, count, truncated, firstTooLarge)
		}
	})

	t.Run("byte limit", func(t *testing.T) {
		text, count, truncated, firstTooLarge := truncateHeadLineWindow(lines, 10, 8)
		if text != "aaa\nbbb\n" || count != 2 || !truncated || firstTooLarge {
			t.Errorf("unexpected: text=%q count=%d truncated=%v firstTooLarge=%v", text, count, truncated, firstTooLarge)
		}
	})

	t.Run("first line exceeds byte limit", func(t *testing.T) {
		text, count, truncated, firstTooLarge := truncateHeadLineWindow(lines, 10, 2)
		if text != "" || count != 0 || !truncated || !firstTooLarge {
			t.Errorf("unexpected: text=%q count=%d truncated=%v firstTooLarge=%v", text, count, truncated, firstTooLarge)
		}
	})

	t.Run("empty input", func(t *testing.T) {
		text, count, truncated, firstTooLarge := truncateHeadLineWindow(nil, 10, 100)
		if text != "" || count != 0 || truncated || firstTooLarge {
			t.Errorf("unexpected: text=%q count=%d truncated=%v firstTooLarge=%v", text, count, truncated, firstTooLarge)
		}
	})
}

func TestTruncateMatchText(t *testing.T) {
	t.Run("short text unchanged", func(t *testing.T) {
		text, truncated := truncateMatchText("hello", 10)
		if text != "hello" || truncated {
			t.Errorf("got %q truncated=%v", text, truncated)
		}
	})

	t.Run("strips trailing newlines", func(t *testing.T) {
		text, truncated := truncateMatchText("hello\r\n", 10)
		if text != "hello" || truncated {
			t.Errorf("got %q truncated=%v", text, truncated)
		}
	})

	t.Run("truncates long text", func(t *testing.T) {
		text, truncated := truncateMatchText("abcdefghij", 5)
		if text != "abcde..." || !truncated {
			t.Errorf("got %q truncated=%v", text, truncated)
		}
	})

	t.Run("exact length not truncated", func(t *testing.T) {
		text, truncated := truncateMatchText("abcde", 5)
		if text != "abcde" || truncated {
			t.Errorf("got %q truncated=%v", text, truncated)
		}
	})

	t.Run("unicode rune boundary", func(t *testing.T) {
		// 3 runes, limit 2 — should cut at rune boundary
		text, truncated := truncateMatchText("日本語", 2)
		if text != "日本..." || !truncated {
			t.Errorf("got %q truncated=%v", text, truncated)
		}
	})
}

func TestFormatMatchPath(t *testing.T) {
	t.Run("relative to workspace", func(t *testing.T) {
		result := formatMatchPath("/home/user/project/src/main.go", "/home/user/project")
		if result != "src/main.go" {
			t.Errorf("got %q", result)
		}
	})

	t.Run("workspace root itself", func(t *testing.T) {
		result := formatMatchPath("/home/user/project", "/home/user/project")
		if result != "." {
			t.Errorf("got %q", result)
		}
	})

	t.Run("outside workspace returns relative with dotdot", func(t *testing.T) {
		dir := t.TempDir()
		result := formatMatchPath("/etc/config.toml", dir)
		// filepath.Rel computes a ../.. path; formatMatchPath converts to slash
		expected, _ := filepath.Rel(dir, "/etc/config.toml")
		expected = filepath.ToSlash(expected)
		if result != expected {
			t.Errorf("got %q, want %q", result, expected)
		}
	})
}

func TestMaxInt(t *testing.T) {
	if maxInt(3, 5) != 5 {
		t.Error("maxInt(3, 5) should be 5")
	}
	if maxInt(5, 3) != 5 {
		t.Error("maxInt(5, 3) should be 5")
	}
	if maxInt(4, 4) != 4 {
		t.Error("maxInt(4, 4) should be 4")
	}
}

func TestNormalizeWorkspaceRoot(t *testing.T) {
	t.Run("valid directory", func(t *testing.T) {
		dir := t.TempDir()
		result, err := normalizeWorkspaceRoot(dir)
		if err != nil {
			t.Fatal(err)
		}
		absDir, _ := filepath.Abs(dir)
		if result != absDir {
			t.Errorf("got %q, want %q", result, absDir)
		}
	})

	t.Run("not a directory", func(t *testing.T) {
		dir := t.TempDir()
		file := filepath.Join(dir, "file.txt")
		os.WriteFile(file, []byte("hi"), 0o644)
		_, err := normalizeWorkspaceRoot(file)
		if err == nil {
			t.Error("expected error for non-directory")
		}
	})

	t.Run("nonexistent path", func(t *testing.T) {
		_, err := normalizeWorkspaceRoot("/nonexistent/path/abc123")
		if err == nil {
			t.Error("expected error for nonexistent path")
		}
	})
}

func TestResolveWorkspacePath(t *testing.T) {
	dir := t.TempDir()

	t.Run("relative path joins with root", func(t *testing.T) {
		result, err := resolveWorkspacePath(dir, "src/main.go")
		if err != nil {
			t.Fatal(err)
		}
		expected, _ := filepath.Abs(filepath.Join(dir, "src/main.go"))
		if result != expected {
			t.Errorf("got %q, want %q", result, expected)
		}
	})

	t.Run("absolute path stays absolute", func(t *testing.T) {
		result, err := resolveWorkspacePath(dir, "/tmp/file.txt")
		if err != nil {
			t.Fatal(err)
		}
		expected, _ := filepath.Abs("/tmp/file.txt")
		if result != expected {
			t.Errorf("got %q, want %q", result, expected)
		}
	})

	t.Run("invalid workspace root fails", func(t *testing.T) {
		_, err := resolveWorkspacePath("/nonexistent/path/abc123", "file.txt")
		if err == nil {
			t.Error("expected error for invalid workspace root")
		}
	})
}
