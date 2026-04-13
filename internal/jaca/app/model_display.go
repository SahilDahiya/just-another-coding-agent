package app

import (
	"strings"

	"jaca/internal/jaca/rpc"
)

func displayModelName(model string) string {
	model = strings.TrimSpace(model)
	if model == "" {
		return ""
	}
	modelID := publicModelID(model)
	access := modelAccessKind(model)
	if access == "" {
		return modelID
	}
	return modelID + " | " + access
}

func publicModelID(model string) string {
	model = strings.TrimSpace(model)
	switch {
	case strings.HasPrefix(model, "openai-responses:"):
		value := strings.TrimPrefix(model, "openai-responses:")
		return strings.TrimSuffix(value, "-chatgpt")
	case strings.HasPrefix(model, "openai-chat:"):
		return strings.TrimPrefix(model, "openai-chat:")
	case strings.HasPrefix(model, "openai:"):
		return strings.TrimPrefix(model, "openai:")
	case strings.HasPrefix(model, "anthropic:"):
		return strings.TrimPrefix(model, "anthropic:")
	default:
		return model
	}
}

func modelAccessKind(model string) string {
	switch {
	case isOpenAICodexOAuthModel(model):
		return "oauth"
	case providerForModel(model) != "":
		return "api"
	default:
		return ""
	}
}

func resolveModelSelection(value string, catalog *rpc.ModelCatalogResponse) string {
	value = strings.TrimSpace(value)
	if value == "" || providerForModel(value) != "" || catalog == nil {
		return value
	}
	normalized := normalizeModelSelectionLabel(value)
	for _, providerCatalog := range catalog.Providers {
		for _, model := range providerCatalog.Models {
			if normalizeModelSelectionLabel(displayModelName(model.ModelID)) == normalized {
				return model.ModelID
			}
		}
	}
	return value
}

func normalizeModelSelectionLabel(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = strings.Join(strings.Fields(value), " ")
	replacer := strings.NewReplacer(" | ", "|", "| ", "|", " |", "|")
	return replacer.Replace(value)
}
