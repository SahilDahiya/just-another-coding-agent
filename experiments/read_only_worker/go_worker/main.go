package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

const (
	protocolVersion = 1
	workerKind      = "read_only"
)

var supportedOperations = []string{"read", "ls", "find", "grep"}

type requestEnvelope struct {
	Type string `json:"type"`
}

type baseMessage struct {
	RequestID string `json:"request_id"`
}

type helloRequest struct {
	baseMessage
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	WorkerKind      string `json:"worker_kind"`
}

type helloResponse struct {
	baseMessage
	Type                 string   `json:"type"`
	ProtocolVersion      int      `json:"protocol_version"`
	WorkerKind           string   `json:"worker_kind"`
	SupportedOperations  []string `json:"supported_operations"`
	SupportsCancel       bool     `json:"supports_cancel"`
	SupportsParallelCall bool     `json:"supports_parallel_calls"`
}

type readRequest struct {
	baseMessage
	Type          string `json:"type"`
	WorkspaceRoot string `json:"workspace_root"`
	Path          string `json:"path"`
	Offset        *int   `json:"offset"`
	Limit         *int   `json:"limit"`
	MaxLines      int    `json:"max_lines"`
	MaxBytes      int    `json:"max_bytes"`
}

type readResult struct {
	baseMessage
	Type                    string `json:"type"`
	WindowText              string `json:"window_text"`
	TotalLines              int    `json:"total_lines"`
	StartLine               int    `json:"start_line"`
	EndLine                 int    `json:"end_line"`
	Truncated               bool   `json:"truncated"`
	NextOffset              *int   `json:"next_offset"`
	FirstLineExceedsMaxBytes bool  `json:"first_line_exceeds_max_bytes"`
}

type lsRequest struct {
	baseMessage
	Type          string  `json:"type"`
	WorkspaceRoot string  `json:"workspace_root"`
	Path          *string `json:"path"`
	Limit         int     `json:"limit"`
	MaxBytes      int     `json:"max_bytes"`
}

type lsEntry struct {
	Name  string `json:"name"`
	IsDir bool   `json:"is_dir"`
}

type lsResult struct {
	baseMessage
	Type         string    `json:"type"`
	Entries      []lsEntry `json:"entries"`
	TotalEntries int       `json:"total_entries"`
	LimitHit     bool      `json:"limit_hit"`
	ByteLimitHit bool      `json:"byte_limit_hit"`
}

type findRequest struct {
	baseMessage
	Type          string  `json:"type"`
	WorkspaceRoot string  `json:"workspace_root"`
	Pattern       string  `json:"pattern"`
	Path          *string `json:"path"`
	Limit         int     `json:"limit"`
	MaxBytes      int     `json:"max_bytes"`
}

type findResult struct {
	baseMessage
	Type         string   `json:"type"`
	Matches      []string `json:"matches"`
	TotalMatches int      `json:"total_matches"`
	LimitHit     bool     `json:"limit_hit"`
	ByteLimitHit bool     `json:"byte_limit_hit"`
}

type grepRequest struct {
	baseMessage
	Type          string  `json:"type"`
	WorkspaceRoot string  `json:"workspace_root"`
	Pattern       string  `json:"pattern"`
	Path          *string `json:"path"`
	Glob          *string `json:"glob"`
	IgnoreCase    bool    `json:"ignore_case"`
	Literal       bool    `json:"literal"`
	Limit         int     `json:"limit"`
	MaxBytes      int     `json:"max_bytes"`
	MaxLineChars  int     `json:"max_line_chars"`
}

type grepMatch struct {
	Path          string `json:"path"`
	LineNumber    int    `json:"line_number"`
	Text          string `json:"text"`
	TextTruncated bool   `json:"text_truncated"`
}

type grepResult struct {
	baseMessage
	Type           string      `json:"type"`
	Matches        []grepMatch `json:"matches"`
	LimitHit       bool        `json:"limit_hit"`
	ByteLimitHit   bool        `json:"byte_limit_hit"`
	TruncatedLines bool        `json:"truncated_lines"`
}

type cancelRequest struct {
	baseMessage
	Type            string `json:"type"`
	TargetRequestID string `json:"target_request_id"`
}

type shutdownRequest struct {
	baseMessage
	Type string `json:"type"`
}

type errorResponse struct {
	baseMessage
	Type      string `json:"type"`
	ErrorCode string `json:"error_code"`
	Message   string `json:"message"`
}

type worker struct {
	writeMu sync.Mutex
	cancelMu sync.Mutex
	cancels map[string]context.CancelFunc
}

func newWorker() *worker {
	return &worker{
		cancels: make(map[string]context.CancelFunc),
	}
}

func (w *worker) writeJSON(value any) error {
	w.writeMu.Lock()
	defer w.writeMu.Unlock()

	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if _, err := os.Stdout.Write(append(data, '\n')); err != nil {
		return err
	}
	return nil
}

func (w *worker) writeError(requestID string, code string, message string) {
	_ = w.writeJSON(errorResponse{
		baseMessage: baseMessage{RequestID: requestID},
		Type:        "error",
		ErrorCode:   code,
		Message:     message,
	})
}

func (w *worker) setCancel(requestID string, cancel context.CancelFunc) {
	w.cancelMu.Lock()
	defer w.cancelMu.Unlock()
	w.cancels[requestID] = cancel
}

func (w *worker) clearCancel(requestID string) {
	w.cancelMu.Lock()
	defer w.cancelMu.Unlock()
	delete(w.cancels, requestID)
}

func (w *worker) cancelRequest(requestID string) {
	w.cancelMu.Lock()
	cancel := w.cancels[requestID]
	w.cancelMu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func normalizeWorkspaceRoot(root string) (string, error) {
	resolved, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("workspace root is not a directory: %s", resolved)
	}
	return resolved, nil
}

func resolveWorkspacePath(workspaceRoot string, toolPath string) (string, error) {
	root, err := normalizeWorkspaceRoot(workspaceRoot)
	if err != nil {
		return "", err
	}
	if filepath.IsAbs(toolPath) {
		return filepath.Abs(toolPath)
	}
	return filepath.Abs(filepath.Join(root, toolPath))
}

func splitLinesKeepEnds(text string) []string {
	if text == "" {
		return nil
	}
	lines := strings.SplitAfter(text, "\n")
	if lines[len(lines)-1] == "" {
		return lines[:len(lines)-1]
	}
	return lines
}

func truncateHeadLineWindow(lines []string, maxLines int, maxBytes int) (string, int, bool, bool) {
	var output strings.Builder
	outputBytes := 0
	lineCount := 0

	for _, line := range lines {
		if lineCount >= maxLines {
			return output.String(), lineCount, true, false
		}
		lineBytes := len([]byte(line))
		if lineCount == 0 && lineBytes > maxBytes {
			return "", 0, true, true
		}
		if outputBytes+lineBytes > maxBytes {
			return output.String(), lineCount, true, false
		}
		output.WriteString(line)
		outputBytes += lineBytes
		lineCount++
	}

	return output.String(), lineCount, false, false
}

func executeRead(ctx context.Context, req readRequest) (readResult, errorResponse, bool) {
	root, err := normalizeWorkspaceRoot(req.WorkspaceRoot)
	if err != nil {
		return readResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	path, err := resolveWorkspacePath(root, req.Path)
	if err != nil {
		return readResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return readResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	if ctx.Err() != nil {
		return readResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "cancelled",
			Message:     "request cancelled",
		}, false
	}

	lines := splitLinesKeepEnds(string(data))
	if len(lines) == 0 {
		startLine := 1
		if req.Offset != nil {
			startLine = *req.Offset
		}
		if startLine != 1 {
			return readResult{}, errorResponse{
				baseMessage: baseMessage{RequestID: req.RequestID},
				Type:        "error",
				ErrorCode:   "operational_error",
				Message:     fmt.Sprintf("offset %d is beyond end of file (0 lines total)", startLine),
			}, false
		}
		return readResult{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "read_result",
			WindowText:  "",
			TotalLines:  0,
			StartLine:   1,
			EndLine:     1,
		}, errorResponse{}, true
	}

	startLine := 1
	if req.Offset != nil {
		startLine = *req.Offset
	}
	startIndex := startLine - 1
	if startIndex >= len(lines) {
		return readResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "operational_error",
			Message:     fmt.Sprintf("offset %d is beyond end of file (%d lines total)", startLine, len(lines)),
		}, false
	}

	selected := lines[startIndex:]
	if req.Limit != nil && *req.Limit < len(selected) {
		selected = selected[:*req.Limit]
	}

	windowText, lineCount, truncated, firstLineTooLarge := truncateHeadLineWindow(selected, req.MaxLines, req.MaxBytes)
	endLine := startLine + lineCount - 1
	var nextOffset *int
	if truncated || (req.Limit != nil && startIndex+lineCount < len(lines)) {
		value := startIndex + lineCount + 1
		nextOffset = &value
	}

	return readResult{
		baseMessage:              baseMessage{RequestID: req.RequestID},
		Type:                     "read_result",
		WindowText:               windowText,
		TotalLines:               len(lines),
		StartLine:                startLine,
		EndLine:                  maxInt(endLine, startLine),
		Truncated:                truncated,
		NextOffset:               nextOffset,
		FirstLineExceedsMaxBytes: firstLineTooLarge,
	}, errorResponse{}, true
}

func executeLS(ctx context.Context, req lsRequest) (lsResult, errorResponse, bool) {
	root, err := normalizeWorkspaceRoot(req.WorkspaceRoot)
	if err != nil {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}

	toolPath := "."
	if req.Path != nil {
		toolPath = *req.Path
	}
	directory, err := resolveWorkspacePath(root, toolPath)
	if err != nil {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	info, err := os.Stat(directory)
	if err != nil {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	if !info.IsDir() {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     fmt.Sprintf("not a directory: %s", directory),
		}, false
	}

	entries, err := os.ReadDir(directory)
	if err != nil {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	if ctx.Err() != nil {
		return lsResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "cancelled",
			Message:     "request cancelled",
		}, false
	}

	sort.Slice(entries, func(i, j int) bool {
		return strings.ToLower(entries[i].Name()) < strings.ToLower(entries[j].Name())
	})

	result := lsResult{
		baseMessage: baseMessage{RequestID: req.RequestID},
		Type:        "ls_result",
		Entries:     make([]lsEntry, 0, len(entries)),
		TotalEntries: len(entries),
	}

	outputBytes := 0
	for _, entry := range entries {
		if len(result.Entries) >= req.Limit {
			result.LimitHit = true
			break
		}

		name := entry.Name()
		itemBytes := len([]byte(name)) + 1
		if outputBytes+itemBytes > req.MaxBytes {
			result.ByteLimitHit = true
			break
		}

		result.Entries = append(result.Entries, lsEntry{
			Name:  name,
			IsDir: entry.IsDir(),
		})
		outputBytes += itemBytes
	}

	return result, errorResponse{}, true
}

func executeFind(ctx context.Context, req findRequest) (findResult, errorResponse, bool) {
	root, err := normalizeWorkspaceRoot(req.WorkspaceRoot)
	if err != nil {
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}

	searchPathValue := "."
	if req.Path != nil {
		searchPathValue = *req.Path
	}
	searchPath, err := resolveWorkspacePath(root, searchPathValue)
	if err != nil {
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	info, err := os.Stat(searchPath)
	if err != nil {
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	if !info.IsDir() {
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     fmt.Sprintf("not a directory: %s", searchPath),
		}, false
	}

	rgPath, err := exec.LookPath("rg")
	if err != nil {
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     "ripgrep (rg) is not installed",
		}, false
	}

	command := exec.CommandContext(ctx, rgPath, "--files", "--hidden", "--glob", req.Pattern, ".")
	command.Dir = searchPath
	stdout, err := command.Output()
	if err != nil {
		if ctx.Err() != nil {
			return findResult{}, errorResponse{
				baseMessage: baseMessage{RequestID: req.RequestID},
				Type:        "error",
				ErrorCode:   "cancelled",
				Message:     "request cancelled",
			}, false
		}
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return findResult{}, errorResponse{
				baseMessage: baseMessage{RequestID: req.RequestID},
				Type:        "error",
				ErrorCode:   "command_error",
				Message:     strings.TrimSpace(string(exitErr.Stderr)),
			}, false
		}
		return findResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     err.Error(),
		}, false
	}

	lines := strings.Split(strings.TrimSpace(string(stdout)), "\n")
	matches := make([]string, 0, len(lines))
	if strings.TrimSpace(string(stdout)) != "" {
		for _, line := range lines {
			if line == "" {
				continue
			}
			normalized := strings.TrimPrefix(line, "./")
			matches = append(matches, filepath.ToSlash(normalized))
		}
	}
	sort.Slice(matches, func(i, j int) bool {
		return strings.ToLower(matches[i]) < strings.ToLower(matches[j])
	})

	result := findResult{
		baseMessage:  baseMessage{RequestID: req.RequestID},
		Type:         "find_result",
		Matches:      make([]string, 0, len(matches)),
		TotalMatches: len(matches),
	}
	outputBytes := 0
	for _, match := range matches {
		if len(result.Matches) >= req.Limit {
			result.LimitHit = true
			break
		}
		itemBytes := len([]byte(match)) + 1
		if outputBytes+itemBytes > req.MaxBytes {
			result.ByteLimitHit = true
			break
		}
		result.Matches = append(result.Matches, match)
		outputBytes += itemBytes
	}

	return result, errorResponse{}, true
}

func truncateMatchText(text string, maxChars int) (string, bool) {
	stripped := strings.TrimRight(text, "\r\n")
	runes := []rune(stripped)
	if len(runes) <= maxChars {
		return stripped, false
	}
	return string(runes[:maxChars]) + "...", true
}

func formatMatchPath(filePath string, workspaceRoot string) string {
	relative, err := filepath.Rel(workspaceRoot, filePath)
	if err != nil {
		return filepath.ToSlash(filePath)
	}
	return filepath.ToSlash(relative)
}

func executeGrep(ctx context.Context, req grepRequest) (grepResult, errorResponse, bool) {
	root, err := normalizeWorkspaceRoot(req.WorkspaceRoot)
	if err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}

	searchPathValue := "."
	if req.Path != nil {
		searchPathValue = *req.Path
	}
	searchPath, err := resolveWorkspacePath(root, searchPathValue)
	if err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}
	if _, err := os.Stat(searchPath); err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "path_error",
			Message:     err.Error(),
		}, false
	}

	rgPath, err := exec.LookPath("rg")
	if err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     "ripgrep (rg) is not installed",
		}, false
	}

	args := []string{
		"--json",
		"--line-number",
		"--sort",
		"path",
		"--color=never",
		"--hidden",
	}
	if req.IgnoreCase {
		args = append(args, "--ignore-case")
	}
	if req.Literal {
		args = append(args, "--fixed-strings")
	}
	if req.Glob != nil {
		args = append(args, "--glob", *req.Glob)
	}
	args = append(args, req.Pattern, searchPath)

	command := exec.CommandContext(ctx, rgPath, args...)
	command.Dir = root
	stdout, err := command.StdoutPipe()
	if err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     err.Error(),
		}, false
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     err.Error(),
		}, false
	}
	if err := command.Start(); err != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     err.Error(),
		}, false
	}

	type matchEvent struct {
		Type string `json:"type"`
		Data struct {
			Path struct {
				Text string `json:"text"`
			} `json:"path"`
			LineNumber int `json:"line_number"`
			Lines struct {
				Text string `json:"text"`
			} `json:"lines"`
		} `json:"data"`
	}

	result := grepResult{
		baseMessage: baseMessage{RequestID: req.RequestID},
		Type:        "grep_result",
		Matches:     make([]grepMatch, 0),
	}
	outputBytes := 0
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		var event matchEvent
		if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
			return grepResult{}, errorResponse{
				baseMessage: baseMessage{RequestID: req.RequestID},
				Type:        "error",
				ErrorCode:   "protocol_error",
				Message:     err.Error(),
			}, false
		}
		if event.Type != "match" {
			continue
		}
		if len(result.Matches) >= req.Limit {
			result.LimitHit = true
			break
		}

		matchText, textTruncated := truncateMatchText(event.Data.Lines.Text, req.MaxLineChars)
		if textTruncated {
			result.TruncatedLines = true
		}
		matchPath := formatMatchPath(event.Data.Path.Text, root)
		rendered := fmt.Sprintf("%s:%d:%s", matchPath, event.Data.LineNumber, matchText)
		itemBytes := len([]byte(rendered)) + 1
		if outputBytes+itemBytes > req.MaxBytes {
			result.ByteLimitHit = true
			break
		}
		result.Matches = append(result.Matches, grepMatch{
			Path:          matchPath,
			LineNumber:    event.Data.LineNumber,
			Text:          matchText,
			TextTruncated: textTruncated,
		})
		outputBytes += itemBytes
	}

	stderrBytes, _ := io.ReadAll(stderr)
	waitErr := command.Wait()
	if ctx.Err() != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "cancelled",
			Message:     "request cancelled",
		}, false
	}
	if scanner.Err() != nil {
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     scanner.Err().Error(),
		}, false
	}
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) && exitErr.ExitCode() == 1 {
			return result, errorResponse{}, true
		}
		message := strings.TrimSpace(string(stderrBytes))
		if message == "" {
			message = waitErr.Error()
		}
		return grepResult{}, errorResponse{
			baseMessage: baseMessage{RequestID: req.RequestID},
			Type:        "error",
			ErrorCode:   "command_error",
			Message:     message,
		}, false
	}

	return result, errorResponse{}, true
}

func maxInt(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func main() {
	if err := run(); err != nil && !errors.Is(err, io.EOF) {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(1)
	}
}

func run() error {
	worker := newWorker()
	var wg sync.WaitGroup

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		var envelope requestEnvelope
		if err := json.Unmarshal(line, &envelope); err != nil {
			worker.writeError("unknown", "invalid_request", err.Error())
			continue
		}

		switch envelope.Type {
		case "hello":
			var req helloRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			if req.ProtocolVersion != protocolVersion || req.WorkerKind != workerKind {
				worker.writeError(req.RequestID, "protocol_error", "unsupported protocol or worker kind")
				continue
			}
			if err := worker.writeJSON(helloResponse{
				baseMessage:           baseMessage{RequestID: req.RequestID},
				Type:                  "hello_ok",
				ProtocolVersion:       protocolVersion,
				WorkerKind:            workerKind,
				SupportedOperations:   supportedOperations,
				SupportsCancel:        true,
				SupportsParallelCall:  true,
			}); err != nil {
				return err
			}
		case "call_read":
			var req readRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			ctx, cancel := context.WithCancel(context.Background())
			worker.setCancel(req.RequestID, cancel)
			wg.Add(1)
			go func() {
				defer wg.Done()
				defer worker.clearCancel(req.RequestID)
				defer cancel()
				result, errResponse, ok := executeRead(ctx, req)
				if !ok {
					worker.writeJSON(errResponse)
					return
				}
				worker.writeJSON(result)
			}()
		case "call_ls":
			var req lsRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			ctx, cancel := context.WithCancel(context.Background())
			worker.setCancel(req.RequestID, cancel)
			wg.Add(1)
			go func() {
				defer wg.Done()
				defer worker.clearCancel(req.RequestID)
				defer cancel()
				result, errResponse, ok := executeLS(ctx, req)
				if !ok {
					worker.writeJSON(errResponse)
					return
				}
				worker.writeJSON(result)
			}()
		case "call_find":
			var req findRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			ctx, cancel := context.WithCancel(context.Background())
			worker.setCancel(req.RequestID, cancel)
			wg.Add(1)
			go func() {
				defer wg.Done()
				defer worker.clearCancel(req.RequestID)
				defer cancel()
				result, errResponse, ok := executeFind(ctx, req)
				if !ok {
					worker.writeJSON(errResponse)
					return
				}
				worker.writeJSON(result)
			}()
		case "call_grep":
			var req grepRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			ctx, cancel := context.WithCancel(context.Background())
			worker.setCancel(req.RequestID, cancel)
			wg.Add(1)
			go func() {
				defer wg.Done()
				defer worker.clearCancel(req.RequestID)
				defer cancel()
				result, errResponse, ok := executeGrep(ctx, req)
				if !ok {
					worker.writeJSON(errResponse)
					return
				}
				worker.writeJSON(result)
			}()
		case "cancel":
			var req cancelRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
				continue
			}
			worker.cancelRequest(req.TargetRequestID)
		case "shutdown":
			var req shutdownRequest
			if err := json.Unmarshal(line, &req); err != nil {
				worker.writeError("unknown", "invalid_request", err.Error())
			}
			wg.Wait()
			return nil
		default:
			var base baseMessage
			_ = json.Unmarshal(line, &base)
			worker.writeError(base.RequestID, "unsupported_operation", fmt.Sprintf("unsupported request type: %s", envelope.Type))
		}
	}

	wg.Wait()
	return scanner.Err()
}
