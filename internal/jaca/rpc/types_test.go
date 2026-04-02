package rpc

import "testing"

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
			"context_window_used":0.413
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
			"budget_before":{
				"should_compact":true,
				"reason":"over_budget",
				"context_window_tokens":100000,
				"effective_context_window_tokens":92000,
				"output_headroom_tokens":8000,
				"trigger_budget_tokens":64400,
				"prompt_reserve_tokens":24000,
				"estimated_resume_history_tokens":43000,
				"estimated_pre_run_tokens":67000,
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
				"estimated_resume_history_tokens":12000,
				"estimated_pre_run_tokens":36000,
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
