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

type WorkspaceProjectDocsPayload struct{}

type ModelCatalogPayload struct{}

type AuthStatusPayload struct{}

type AuthPrepareFilePayload struct {
	Provider string `json:"provider"`
}

type AuthSetPayload struct {
	Provider string `json:"provider"`
	Secret   string `json:"secret"`
	Storage  string `json:"storage"`
}

type AuthClearPayload struct {
	Provider string `json:"provider"`
}

type AuthLoginOpenAICodexStartPayload struct{}

type AuthLoginOpenAICodexCompletePayload struct {
	FlowID         string `json:"flow_id"`
	CallbackOrCode string `json:"callback_or_code"`
}

type AuthLoginOpenAICodexWaitPayload struct {
	FlowID string `json:"flow_id"`
}

type SessionCompactPayload struct {
	SessionID string `json:"session_id"`
}

type RunStartPayload struct {
	SessionID string      `json:"session_id"`
	Prompt    string      `json:"prompt"`
	Thinking  interface{} `json:"thinking,omitempty"`
}

type RunStartResponse struct {
	SessionID string `json:"session_id"`
}

type RunEnqueuePayload struct {
	SessionID string `json:"session_id"`
	Prompt    string `json:"prompt"`
	Mode      string `json:"mode,omitempty"`
}

type RunInterruptPayload struct {
	SessionID          string `json:"session_id"`
	PromoteQueuedSteer bool   `json:"promote_queued_steer,omitempty"`
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
	SessionID   string                `json:"session_id"`
	ProjectDocs []WorkspaceProjectDoc `json:"project_docs"`
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

type WorkspaceProjectDoc struct {
	Path      string `json:"path"`
	Filename  string `json:"filename"`
	Truncated bool   `json:"truncated"`
}

type WorkspaceProjectDocsResponse struct {
	Documents []WorkspaceProjectDoc `json:"documents"`
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
	Provider         string `json:"provider"`
	Configured       bool   `json:"configured"`
	SecretConfigured bool   `json:"secret_configured"`
	RequiresSecret   bool   `json:"requires_secret"`
	Source           string `json:"source"`
	EnvKey           string `json:"env_key"`
	Reason           string `json:"reason"`
}

type LocalSecretStoreStatus struct {
	Available     bool    `json:"available"`
	Message       *string `json:"message"`
	FileStorePath string  `json:"file_store_path"`
}

type OAuthProviderStatus struct {
	Provider  string  `json:"provider"`
	LoggedIn  bool    `json:"logged_in"`
	AccountID *string `json:"account_id"`
	ExpiresAt *int64  `json:"expires_at"`
}

type AuthStatusResponse struct {
	Providers        []AuthProviderStatus   `json:"providers"`
	LocalSecretStore LocalSecretStoreStatus `json:"local_secret_store"`
	OAuthProviders   []OAuthProviderStatus  `json:"oauth_providers"`
}

type AuthPrepareFileResponse struct {
	Provider     string `json:"provider"`
	EnvKey       string `json:"env_key"`
	FilePath     string `json:"file_path"`
	Created      bool   `json:"created"`
	FileSnippet  string `json:"file_snippet"`
	EntrySnippet string `json:"entry_snippet"`
}

type AuthSetResponse struct {
	Status AuthProviderStatus `json:"status"`
}

type AuthClearResponse struct {
	Status AuthProviderStatus `json:"status"`
}

type AuthLoginOpenAICodexStartResponse struct {
	FlowID       string `json:"flow_id"`
	AuthURL      string `json:"auth_url"`
	Instructions string `json:"instructions"`
}

type AuthLoginOpenAICodexCompleteResponse struct {
	Status OAuthProviderStatus `json:"status"`
}

type AuthLoginOpenAICodexWaitResponse struct {
	Status OAuthProviderStatus `json:"status"`
}

type RunEnqueueResponse struct {
	SessionID   string `json:"session_id"`
	QueuedCount int    `json:"queued_count"`
}

type RunInterruptResponse struct {
	SessionID     string `json:"session_id"`
	PromotedCount int    `json:"promoted_count"`
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
	Type                   string                `json:"type"`
	RunID                  string                `json:"run_id"`
	CompactionID           string                `json:"compaction_id,omitempty"`
	CompactionCount        *int                  `json:"compaction_count,omitempty"`
	SummarizedThrough      string                `json:"summarized_through_run_id,omitempty"`
	Delta                  string                `json:"delta,omitempty"`
	ToolCallID             string                `json:"tool_call_id,omitempty"`
	ToolName               string                `json:"tool_name,omitempty"`
	Args                   map[string]any        `json:"args,omitempty"`
	ArgsValid              *bool                 `json:"args_valid,omitempty"`
	Result                 any                   `json:"result,omitempty"`
	Partial                any                   `json:"partial_result,omitempty"`
	ErrorType              string                `json:"error_type,omitempty"`
	Message                string                `json:"message,omitempty"`
	OutputText             string                `json:"output_text,omitempty"`
	InputTokens            *int                  `json:"input_tokens,omitempty"`
	OutputTokens           *int                  `json:"output_tokens,omitempty"`
	TotalTokens            *int                  `json:"total_tokens,omitempty"`
	ContextWindowUsed      *float64              `json:"context_window_used,omitempty"`
	NextRequestContextUsed *float64              `json:"next_request_context_window_used,omitempty"`
	NextPrompts            []string              `json:"next_prompts,omitempty"`
	LaterPrompts           []string              `json:"later_prompts,omitempty"`
	Prompts                []string              `json:"prompts,omitempty"`
	Mode                   string                `json:"mode,omitempty"`
	Activity               *ToolActivity         `json:"activity,omitempty"`
	TranscriptSummary      *RunTranscriptSummary `json:"transcript_summary,omitempty"`
}

type ToolActivity struct {
	Title        string         `json:"title"`
	DisplayLabel *string        `json:"display_label,omitempty"`
	Summary      *string        `json:"summary"`
	DurationMS   *int           `json:"duration_ms"`
	Details      map[string]any `json:"details"`
	GroupKind    *string        `json:"group_kind"`
}

type ActivityGroupCounts struct {
	Read   int `json:"read"`
	Search int `json:"search"`
	List   int `json:"list"`
	Shell  int `json:"shell"`
	Write  int `json:"write"`
	Edit   int `json:"edit"`
	Tool   int `json:"tool"`
}

type ActivityGroupSummary struct {
	GroupKind   string              `json:"group_kind"`
	GroupLabel  string              `json:"group_label"`
	GroupCounts ActivityGroupCounts `json:"group_counts"`
	DisplayHint *string             `json:"display_hint"`
	Outcome     string              `json:"outcome"`
	ElapsedMS   *int                `json:"elapsed_ms"`
}

type RunTranscriptSummary struct {
	ElapsedMS                    int                    `json:"elapsed_ms"`
	ToolCallCount                int                    `json:"tool_call_count"`
	ToolDurationMS               int                    `json:"tool_duration_ms"`
	InputTokens                  *int                   `json:"input_tokens"`
	OutputTokens                 *int                   `json:"output_tokens"`
	TotalTokens                  *int                   `json:"total_tokens"`
	ContextWindowUsed            *float64               `json:"context_window_used"`
	NextRequestContextWindowUsed *float64               `json:"next_request_context_window_used"`
	HadWorkActivity              bool                   `json:"had_work_activity"`
	ShouldShowSeparator          bool                   `json:"should_show_separator"`
	ActivityGroups               []ActivityGroupSummary `json:"activity_groups"`
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
