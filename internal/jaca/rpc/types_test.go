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
	if envelope.Event.Activity.GroupKind == nil || *envelope.Event.Activity.GroupKind != "exploration" {
		t.Fatalf("GroupKind = %v, want exploration", envelope.Event.Activity.GroupKind)
	}
}
