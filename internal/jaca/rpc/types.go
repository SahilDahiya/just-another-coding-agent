package rpc

import (
	"encoding/json"
	"fmt"
)

type Request struct {
	ID      string `json:"id"`
	Command string `json:"command"`
	Payload any    `json:"payload"`
}

type SessionCreatePayload struct{}

type SessionNamePayload struct {
	SessionID string `json:"session_id"`
	Name      string `json:"name"`
}

type SessionPreviewPayload struct {
	SessionID string `json:"session_id"`
}

type ModelCatalogPayload struct{}

type AuthStatusPayload struct{}

type AuthSetPayload struct {
	Provider string `json:"provider"`
	Secret   string `json:"secret"`
	Storage  string `json:"storage"`
}

type AuthClearPayload struct {
	Provider string `json:"provider"`
}

type SessionCompactPayload struct {
	SessionID string `json:"session_id"`
}

type RunStartPayload struct {
	SessionID string      `json:"session_id"`
	Prompt    string      `json:"prompt"`
	Thinking  interface{} `json:"thinking,omitempty"`
}

type EnvelopeType string

const (
	EnvelopeResponse EnvelopeType = "rpc_response"
	EnvelopeEvent    EnvelopeType = "rpc_event"
	EnvelopeError    EnvelopeType = "rpc_error"
)

type envelopeHeader struct {
	Type EnvelopeType `json:"type"`
	ID   string       `json:"id"`
}

type ErrorEnvelope struct {
	Type      EnvelopeType `json:"type"`
	ID        *string      `json:"id"`
	ErrorType string       `json:"error_type"`
	Message   string       `json:"message"`
}

type SessionCreateResponse struct {
	SessionID string `json:"session_id"`
}

type SessionNameResponse struct {
	SessionID string `json:"session_id"`
	Name      string `json:"name"`
}

type SessionPreviewEntry struct {
	Kind string `json:"kind"`
	Text string `json:"text"`
}

type SessionPreviewResponse struct {
	SessionID string                `json:"session_id"`
	Entries   []SessionPreviewEntry `json:"entries"`
	Truncated bool                  `json:"truncated"`
}

type ModelCatalogModel struct {
	ModelID     string `json:"model_id"`
	Description string `json:"description"`
}

type ModelCatalogProvider struct {
	Provider       string              `json:"provider"`
	DefaultModelID string              `json:"default_model_id"`
	Models         []ModelCatalogModel `json:"models"`
}

type ModelCatalogResponse struct {
	Providers []ModelCatalogProvider `json:"providers"`
}

type AuthProviderStatus struct {
	Provider   string `json:"provider"`
	Configured bool   `json:"configured"`
	Source     string `json:"source"`
	EnvKey     string `json:"env_key"`
}

type LocalSecretStoreStatus struct {
	Available     bool    `json:"available"`
	Message       *string `json:"message"`
	FileStorePath string  `json:"file_store_path"`
}

type AuthStatusResponse struct {
	Providers        []AuthProviderStatus   `json:"providers"`
	LocalSecretStore LocalSecretStoreStatus `json:"local_secret_store"`
}

type AuthSetResponse struct {
	Status AuthProviderStatus `json:"status"`
}

type AuthClearResponse struct {
	Status AuthProviderStatus `json:"status"`
}

type SessionCompactSummary struct {
	CurrentObjective *string  `json:"current_objective"`
	EstablishedFacts []string `json:"established_facts"`
	UserPreferences  []string `json:"user_preferences"`
	ImportantPaths   []string `json:"important_paths"`
	OpenQuestions    []string `json:"open_questions"`
	UnresolvedWork   []string `json:"unresolved_work"`
}

type SessionCompactResponse struct {
	CompactionID         string                `json:"compaction_id"`
	SummarizedThroughRun string                `json:"summarized_through_run_id"`
	Summary              SessionCompactSummary `json:"summary"`
}

type ResponseEnvelope struct {
	Type     EnvelopeType    `json:"type"`
	ID       string          `json:"id"`
	Response json.RawMessage `json:"response"`
}

type EventEnvelope struct {
	Type  EnvelopeType `json:"type"`
	ID    string       `json:"id"`
	Event RunEvent     `json:"event"`
}

type RunEvent struct {
	Type              string         `json:"type"`
	RunID             string         `json:"run_id"`
	CompactionID      string         `json:"compaction_id,omitempty"`
	CompactionCount   *int           `json:"compaction_count,omitempty"`
	SummarizedThrough string         `json:"summarized_through_run_id,omitempty"`
	Delta             string         `json:"delta,omitempty"`
	ToolCallID        string         `json:"tool_call_id,omitempty"`
	ToolName          string         `json:"tool_name,omitempty"`
	Args              map[string]any `json:"args,omitempty"`
	ArgsValid         *bool          `json:"args_valid,omitempty"`
	Result            any            `json:"result,omitempty"`
	Partial           any            `json:"partial_result,omitempty"`
	ErrorType         string         `json:"error_type,omitempty"`
	Message           string         `json:"message,omitempty"`
	OutputText        string         `json:"output_text,omitempty"`
	InputTokens       *int           `json:"input_tokens,omitempty"`
	OutputTokens      *int           `json:"output_tokens,omitempty"`
	TotalTokens       *int           `json:"total_tokens,omitempty"`
	ContextWindowUsed *float64       `json:"context_window_used,omitempty"`
	Activity          *ToolActivity  `json:"activity,omitempty"`
}

type ToolActivity struct {
	Title        string         `json:"title"`
	DisplayLabel *string        `json:"display_label,omitempty"`
	Summary      *string        `json:"summary"`
	DurationMS   *int           `json:"duration_ms"`
	Details      map[string]any `json:"details"`
	GroupKind    *string        `json:"group_kind"`
}

func decodeEnvelope(line []byte) (any, error) {
	var header envelopeHeader
	if err := json.Unmarshal(line, &header); err != nil {
		return nil, err
	}
	switch header.Type {
	case EnvelopeResponse:
		var envelope ResponseEnvelope
		if err := json.Unmarshal(line, &envelope); err != nil {
			return nil, err
		}
		return envelope, nil
	case EnvelopeEvent:
		var envelope EventEnvelope
		if err := json.Unmarshal(line, &envelope); err != nil {
			return nil, err
		}
		return envelope, nil
	case EnvelopeError:
		var envelope ErrorEnvelope
		if err := json.Unmarshal(line, &envelope); err != nil {
			return nil, err
		}
		return envelope, nil
	default:
		return nil, fmt.Errorf("unknown envelope type: %s", header.Type)
	}
}
