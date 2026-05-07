package rpc

import (
	"errors"
	"strings"
	"testing"
)

func TestDecodeEnvelopePreservesRunSucceededUsageFields(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-1",
		"event":{
			"type":"run_succeeded",
			"run_id":"run-1",
			"output_text":"done",
			"input_tokens":120,
			"output_tokens":45,
			"total_tokens":165,
			"context_window_used":0.413,
			"next_request_context_window_used":0.07
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}

	if envelope.Event.InputTokens == nil || *envelope.Event.InputTokens != 120 {
		t.Fatalf("InputTokens = %v, want 120", envelope.Event.InputTokens)
	}
	if envelope.Event.OutputTokens == nil || *envelope.Event.OutputTokens != 45 {
		t.Fatalf("OutputTokens = %v, want 45", envelope.Event.OutputTokens)
	}
	if envelope.Event.TotalTokens == nil || *envelope.Event.TotalTokens != 165 {
		t.Fatalf("TotalTokens = %v, want 165", envelope.Event.TotalTokens)
	}
	if envelope.Event.ContextWindowUsed == nil || *envelope.Event.ContextWindowUsed != 0.413 {
		t.Fatalf("ContextWindowUsed = %v, want 0.413", envelope.Event.ContextWindowUsed)
	}
	if envelope.Event.NextRequestContextUsed == nil || *envelope.Event.NextRequestContextUsed != 0.07 {
		t.Fatalf("NextRequestContextUsed = %v, want 0.07", envelope.Event.NextRequestContextUsed)
	}
}

func TestDecodeEnvelopePreservesRunSucceededTranscriptSummary(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-1",
		"event":{
			"type":"run_succeeded",
			"run_id":"run-1",
			"output_text":"done",
			"transcript_summary":{
				"elapsed_ms":179000,
				"tool_call_count":5,
				"tool_duration_ms":1234,
				"input_tokens":80000,
				"output_tokens":2000,
				"total_tokens":82000,
				"context_window_used":0.41,
				"next_request_context_window_used":0.43,
				"had_work_activity":true,
				"should_show_separator":true,
				"activity_groups":[
					{
						"group_kind":"execution",
						"group_label":"Git check",
						"group_counts":{"shell":5,"tool":5},
						"display_hint":"git status --short",
						"outcome":"success",
						"elapsed_ms":1234
					}
				]
			}
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	summary := envelope.Event.TranscriptSummary
	if summary == nil {
		t.Fatal("TranscriptSummary = nil, want non-nil")
	}
	if summary.ElapsedMS != 179000 {
		t.Fatalf("ElapsedMS = %d, want 179000", summary.ElapsedMS)
	}
	if !summary.ShouldShowSeparator {
		t.Fatal("ShouldShowSeparator = false, want true")
	}
	if summary.TotalTokens == nil || *summary.TotalTokens != 82000 {
		t.Fatalf("TotalTokens = %v, want 82000", summary.TotalTokens)
	}
	if len(summary.ActivityGroups) != 1 {
		t.Fatalf("ActivityGroups len = %d, want 1", len(summary.ActivityGroups))
	}
	group := summary.ActivityGroups[0]
	if group.GroupKind != "execution" || group.GroupLabel != "Git check" {
		t.Fatalf("group identity = %q/%q, want execution/Git check", group.GroupKind, group.GroupLabel)
	}
	if group.GroupCounts.Shell != 5 || group.GroupCounts.Tool != 5 {
		t.Fatalf("group counts = %+v, want shell=5 tool=5", group.GroupCounts)
	}
	if group.DisplayHint == nil || *group.DisplayHint != "git status --short" {
		t.Fatalf("DisplayHint = %v, want git status --short", group.DisplayHint)
	}
}

func TestDecodeEnvelopePreservesToolActivityGroupKind(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-2",
		"event":{
			"type":"tool_call_started",
			"run_id":"run-1",
			"tool_call_id":"tool-1",
			"tool_name":"read",
			"activity":{
				"title":"read AGENTS.md",
				"display_label":"Read",
				"group_kind":"exploration",
				"details":{
					"kind":"read",
					"path":"AGENTS.md"
				}
			}
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	if envelope.Event.Activity == nil {
		t.Fatal("Activity = nil, want non-nil")
	}
	if envelope.Event.Activity.DisplayLabel == nil || *envelope.Event.Activity.DisplayLabel != "Read" {
		t.Fatalf("DisplayLabel = %v, want Read", envelope.Event.Activity.DisplayLabel)
	}
	if envelope.Event.Activity.GroupKind == nil || *envelope.Event.Activity.GroupKind != "exploration" {
		t.Fatalf("GroupKind = %v, want exploration", envelope.Event.Activity.GroupKind)
	}
}

func TestDecodeEnvelopePreservesSessionCompactionCompletedFields(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-3",
		"event":{
			"type":"session_compaction_completed",
			"compaction_id":"compact-1",
			"summarized_through_run_id":"run-5",
			"first_kept_run_id":"run-6",
			"checkpoint_through_run_id":"run-6",
			"estimated_tokens_saved":31000,
			"estimated_percent_saved":0.7209,
			"estimated_headroom_gain_tokens":31000,
			"budget_before":{
				"should_compact":true,
				"reason":"over_budget",
				"context_window_tokens":100000,
				"effective_context_window_tokens":92000,
				"output_headroom_tokens":8000,
				"trigger_budget_tokens":64400,
				"prompt_reserve_tokens":24000,
				"estimated_resume_message_tokens":42700,
				"estimated_resume_history_tokens":43000,
				"estimated_checkpoint_tokens":900,
				"estimated_summary_tokens":300,
				"estimated_pre_run_tokens":67000,
				"estimated_post_compaction_headroom_tokens":25000,
				"measured_usage_tokens":120,
				"estimated_trailing_tokens":42880,
				"runs_since_latest_compaction":2
			},
			"budget_after":{
				"should_compact":false,
				"reason":"no_new_work",
				"context_window_tokens":100000,
				"effective_context_window_tokens":92000,
				"output_headroom_tokens":8000,
				"trigger_budget_tokens":64400,
				"prompt_reserve_tokens":24000,
				"estimated_resume_message_tokens":11000,
				"estimated_resume_history_tokens":12000,
				"estimated_checkpoint_tokens":6000,
				"estimated_summary_tokens":1000,
				"estimated_pre_run_tokens":36000,
				"estimated_post_compaction_headroom_tokens":56000,
				"runs_since_latest_compaction":0
			}
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	if envelope.Event.Type != "session_compaction_completed" {
		t.Fatalf("Type = %q, want session_compaction_completed", envelope.Event.Type)
	}
	if envelope.Event.CompactionID != "compact-1" {
		t.Fatalf("CompactionID = %q, want compact-1", envelope.Event.CompactionID)
	}
	if envelope.Event.SummarizedThrough != "run-5" {
		t.Fatalf("SummarizedThrough = %q, want run-5", envelope.Event.SummarizedThrough)
	}
}

func TestDecodeEnvelopePreservesSessionCompactionWarningFields(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-4",
		"event":{
			"type":"session_compaction_warning",
			"compaction_count":2,
			"message":"Session has been compacted multiple times; continuity quality may degrade."
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	if envelope.Event.Type != "session_compaction_warning" {
		t.Fatalf("Type = %q, want session_compaction_warning", envelope.Event.Type)
	}
	if envelope.Event.CompactionCount == nil || *envelope.Event.CompactionCount != 2 {
		t.Fatalf("CompactionCount = %v, want 2", envelope.Event.CompactionCount)
	}
	if envelope.Event.Message != "Session has been compacted multiple times; continuity quality may degrade." {
		t.Fatalf("Message = %q, want repeated compaction warning", envelope.Event.Message)
	}
}

func TestDecodeEnvelopePreservesApprovalEventFields(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-5",
		"event":{
			"type":"approval_requested",
			"run_id":"run-1",
			"tool_name":"shell",
			"tool_call_id":"tool-1",
			"request":{
				"request_id":"approval-1",
				"request_kind":"command_execution",
				"reason":"allow shell command: ls",
				"command":"ls",
				"cwd":"/workspace",
				"shell_family":"posix",
				"requested_capabilities":{
					"filesystem_access":"full_access",
					"network_access":"enabled",
					"execution_isolation":"unsandboxed",
					"approval_mode":"always"
				}
			}
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	if envelope.Event.Request == nil {
		t.Fatal("Request = nil, want non-nil")
	}
	if envelope.Event.Request.RequestID != "approval-1" {
		t.Fatalf("RequestID = %q, want approval-1", envelope.Event.Request.RequestID)
	}
	if envelope.Event.Request.RequestKind != "command_execution" {
		t.Fatalf("RequestKind = %q, want command_execution", envelope.Event.Request.RequestKind)
	}
	if envelope.Event.ToolName != "shell" {
		t.Fatalf("ToolName = %q, want shell", envelope.Event.ToolName)
	}
	if envelope.Event.Request.RequestedCapabilities.ApprovalMode != "always" {
		t.Fatalf("ApprovalMode = %q, want always", envelope.Event.Request.RequestedCapabilities.ApprovalMode)
	}
}

func TestDecodeEnvelopePreservesOnboardingQuestionEventFields(t *testing.T) {
	line := []byte(`{
		"type":"rpc_event",
		"id":"req-6",
		"event":{
			"type":"onboarding_question_requested",
			"run_id":"run-1",
			"attempt_id":"attempt-1",
			"question_type":"mcq",
			"prompt":"Which file defines the slash command table?",
			"options":[
				"internal/jaca/app/model.go",
				"internal/jaca/app/slash.go",
				"internal/jaca/app/render.go",
				"internal/jaca/rpc/client.go"
			]
		}
	}`)

	value, err := decodeEnvelope(line)
	if err != nil {
		t.Fatalf("decodeEnvelope() returned error: %v", err)
	}

	envelope, ok := value.(EventEnvelope)
	if !ok {
		t.Fatalf("decodeEnvelope() type = %T, want EventEnvelope", value)
	}
	if envelope.Event.AttemptID != "attempt-1" {
		t.Fatalf("AttemptID = %q, want attempt-1", envelope.Event.AttemptID)
	}
	if envelope.Event.QuestionType != "mcq" {
		t.Fatalf("QuestionType = %q, want mcq", envelope.Event.QuestionType)
	}
	if envelope.Event.Prompt != "Which file defines the slash command table?" {
		t.Fatalf("Prompt = %q, want onboarding prompt", envelope.Event.Prompt)
	}
	if len(envelope.Event.Options) != 4 || envelope.Event.Options[1] != "internal/jaca/app/slash.go" {
		t.Fatalf("Options = %#v, want slash.go as second option", envelope.Event.Options)
	}
}

func TestDecodeEnvelopeWrapsParseErrorWithBytePreview(t *testing.T) {
	// Regression: a parse failure on the backend stdout stream used to
	// surface as a raw encoding/json error with no context. The enriched
	// DecodeError now carries the offending line length, a hex dump of
	// the leading bytes, and a printable preview so a single bug report
	// identifies the bad bytes unambiguously.
	line := []byte("Not a JSON line at all\n")

	_, err := decodeEnvelope(line)
	if err == nil {
		t.Fatal("decodeEnvelope() returned nil error for a non-JSON line")
	}

	var decodeErr *DecodeError
	if !errors.As(err, &decodeErr) {
		t.Fatalf("err type = %T, want *DecodeError", err)
	}
	if decodeErr.Err == nil {
		t.Fatal("DecodeError.Err must wrap the underlying json error")
	}
	if string(decodeErr.Line) != string(line) {
		t.Errorf("DecodeError.Line = %q, want %q", decodeErr.Line, line)
	}

	msg := decodeErr.Error()
	// "Not" in ASCII is 4e 6f 74 — verify the hex dump is in the message.
	if !strings.Contains(msg, "4e6f74") {
		t.Errorf("error message missing hex prefix 4e6f74: %s", msg)
	}
	// The printable preview should include the original string so
	// a reader can tell at a glance what leaked.
	if !strings.Contains(msg, "Not a JSON line at all") {
		t.Errorf("error message missing printable preview: %s", msg)
	}
	// Length must be reported.
	if !strings.Contains(msg, "line=23 bytes") {
		t.Errorf("error message missing line length: %s", msg)
	}
}

func TestDecodeEnvelopeTruncatesLongPreview(t *testing.T) {
	// A pathological leak (e.g., a multi-megabyte traceback printed to
	// stdout) must not dump the whole thing into the error message.
	long := make([]byte, 10_000)
	for i := range long {
		long[i] = 'A'
	}

	_, err := decodeEnvelope(long)
	if err == nil {
		t.Fatal("decodeEnvelope() returned nil error for a non-JSON line")
	}

	var decodeErr *DecodeError
	if !errors.As(err, &decodeErr) {
		t.Fatalf("err type = %T, want *DecodeError", err)
	}
	msg := decodeErr.Error()
	if !strings.Contains(msg, "truncated from 10000") {
		t.Errorf("error message missing truncation notice: %s", msg)
	}
	// The preview string length in the error message should be capped
	// at the max preview window plus framing overhead, not the full
	// 10 KB.
	if len(msg) > 1024 {
		t.Errorf("error message too large (%d bytes); expected capped preview", len(msg))
	}
}

func TestDecodeErrorUnwrapsForErrorsIs(t *testing.T) {
	sentinel := errors.New("boom")
	decodeErr := &DecodeError{Err: sentinel, Line: []byte("bad")}
	if !errors.Is(decodeErr, sentinel) {
		t.Error("errors.Is should see the wrapped error via Unwrap")
	}
}

func TestDecodeEnvelopeDoesNotShareReferenceWithCallerSlice(t *testing.T) {
	// bufio.Scanner.Bytes reuses its backing buffer between Scan() calls.
	// If DecodeError holds a reference instead of a defensive copy, the
	// captured line will be corrupted the next time the scanner advances.
	original := []byte("Not JSON")
	_, err := decodeEnvelope(original)

	var decodeErr *DecodeError
	if !errors.As(err, &decodeErr) {
		t.Fatalf("err type = %T, want *DecodeError", err)
	}
	// Mutate the caller's slice to simulate bufio reuse.
	for i := range original {
		original[i] = 'X'
	}
	if string(decodeErr.Line) != "Not JSON" {
		t.Errorf(
			"DecodeError.Line was corrupted by caller mutation: %q (expected %q)",
			decodeErr.Line, "Not JSON",
		)
	}
}
