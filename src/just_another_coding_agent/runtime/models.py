from __future__ import annotations

import os
from typing import Any

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider


def resolve_canonical_model(model: Any) -> Model:
    if isinstance(model, Model):
        return model

    if isinstance(model, str):
        if model.startswith("openai-responses:"):
            return _build_openai_responses_model(model)
        if model.startswith("openai:") or model.startswith("openai-chat:"):
            return _build_openai_chat_model(model)

    return infer_model(model)


def _build_openai_responses_model(model_id: str) -> OpenAIResponsesModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIResponsesModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_chat_model(model_id: str) -> OpenAIChatModel:
    _, model_name = model_id.split(":", 1)
    return OpenAIChatModel(
        model_name,
        provider=_build_openai_provider(),
    )


def _build_openai_provider() -> OpenAIProvider:
    return OpenAIProvider(
        base_url=os.environ.get("OPENAI_BASE_URL"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


__all__ = ["resolve_canonical_model"]
