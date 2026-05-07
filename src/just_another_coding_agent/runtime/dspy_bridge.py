from __future__ import annotations

import asyncio
import os
from types import MethodType, SimpleNamespace
from typing import Any

from openai import OpenAI

from just_another_coding_agent.auth import (
    resolve_openai_codex_oauth_credentials_sync,
    resolve_provider_secret,
)
from just_another_coding_agent.provider_readiness import (
    ProviderReadinessError,
    compute_model_readiness,
)
from just_another_coding_agent.runtime.models import (
    OPENAI_CODEX_BASE_URL,
    OPENAI_CODEX_MODEL_NAME_BY_ID,
    get_external_model_id,
)


def import_dspy() -> Any:
    try:
        import dspy
    except ImportError as error:  # pragma: no cover - exercised in runtime only
        raise RuntimeError(
            "DSPy is required for /onboard question generation. Install the 'dspy' "
            "package before using onboarding."
        ) from error
    return dspy


def build_dspy_lm(*, dspy: Any, model: Any) -> Any:
    model_id = resolve_dspy_model_id(model)
    readiness = compute_model_readiness(model_id)
    if not readiness.configured:
        raise ProviderReadinessError(
            f"{readiness.provider} is not ready: {readiness.reason}"
        )

    if model_id.startswith("openai-responses:"):
        model_name = _resolve_dspy_openai_responses_model_name(model_id)
        kwargs = _build_openai_responses_kwargs(model_id)
        lm = dspy.LM(f"openai/{model_name}", **kwargs)
        if _is_openai_codex_oauth_lane(model_id):
            return _wrap_codex_responses_lm(lm)
        return lm

    if model_id.startswith("openai-chat:"):
        model_name = model_id.split(":", 1)[1]
        kwargs = {
            "api_key": resolve_provider_secret("openai"),
            "model_type": "chat",
        }
        api_base = os.environ.get("OPENAI_BASE_URL")
        if api_base:
            kwargs["api_base"] = api_base
        return dspy.LM(f"openai/{model_name}", **kwargs)

    if model_id.startswith("anthropic:"):
        model_name = model_id.split(":", 1)[1]
        return dspy.LM(
            f"anthropic/{model_name}",
            api_key=resolve_provider_secret("anthropic"),
        )

    raise RuntimeError(f"Unsupported onboarding generation model: {model_id}")


def resolve_dspy_model_id(model: Any) -> str:
    if isinstance(model, str) and model.strip():
        return model
    external_model_id = get_external_model_id(model)
    if external_model_id is not None:
        return external_model_id
    raise RuntimeError("Could not resolve a canonical model id for DSPy")


def _build_openai_responses_kwargs(model_id: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model_type": "responses"}
    if _is_openai_codex_oauth_lane(model_id):
        credentials = resolve_openai_codex_oauth_credentials_sync()
        if credentials is None:
            raise ProviderReadinessError(
                f"ChatGPT subscription login required for {model_id}"
            )
        kwargs["api_key"] = credentials.access
        kwargs["api_base"] = OPENAI_CODEX_BASE_URL
        kwargs["extra_headers"] = {
            "chatgpt-account-id": credentials.account_id,
            "originator": "jaca",
            "OpenAI-Beta": "responses=experimental",
        }
        kwargs["store"] = False
        return kwargs

    kwargs["api_key"] = resolve_provider_secret("openai")
    api_base = os.environ.get("OPENAI_BASE_URL")
    if api_base:
        kwargs["api_base"] = api_base
    return kwargs


def _resolve_dspy_openai_responses_model_name(model_id: str) -> str:
    _, model_name = model_id.split(":", 1)
    if model_id.endswith("-chatgpt"):
        resolved = OPENAI_CODEX_MODEL_NAME_BY_ID.get(model_name)
        if resolved is None:
            raise RuntimeError(f"Unsupported onboarding generation model: {model_id}")
        return resolved
    return model_name


def _is_openai_codex_oauth_lane(model_id: str) -> bool:
    return model_id.startswith("openai-responses:") and model_id.endswith("-chatgpt")


def _wrap_codex_responses_lm(lm: Any) -> Any:
    if not hasattr(lm, "forward") or not hasattr(lm, "aforward"):
        return lm

    def forward(self, prompt: str | None = None, messages=None, **kwargs):
        messages, kwargs = _prepare_codex_responses_call(
            lm=self,
            prompt=prompt,
            messages=messages,
            kwargs=kwargs,
        )
        merged_kwargs = {**self.kwargs, **kwargs}
        return _stream_codex_response(
            model=self.model,
            messages=messages,
            kwargs=merged_kwargs,
        )

    async def aforward(self, prompt: str | None = None, messages=None, **kwargs):
        messages, kwargs = _prepare_codex_responses_call(
            lm=self,
            prompt=prompt,
            messages=messages,
            kwargs=kwargs,
        )
        merged_kwargs = {**self.kwargs, **kwargs}
        return await asyncio.to_thread(
            _stream_codex_response,
            model=self.model,
            messages=messages,
            kwargs=merged_kwargs,
        )

    lm.forward = MethodType(forward, lm)
    lm.aforward = MethodType(aforward, lm)
    return lm


def _prepare_codex_responses_call(
    *,
    lm: Any,
    prompt: str | None,
    messages: list[dict[str, Any]] | None,
    kwargs: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "instructions" in kwargs or "instructions" in getattr(lm, "kwargs", {}):
        return list(messages or [{"role": "user", "content": prompt}]), kwargs

    working_messages = list(messages or [{"role": "user", "content": prompt}])
    if lm.use_developer_role and lm.model_type == "responses":
        working_messages = [
            {**message, "role": "developer"}
            if message.get("role") == "system"
            else message
            for message in working_messages
        ]
    instructions, content_messages = _extract_codex_instructions(working_messages)
    updated_kwargs = dict(kwargs)
    updated_kwargs["instructions"] = instructions
    return content_messages, updated_kwargs


def _extract_codex_instructions(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    instruction_parts: list[str] = []
    content_messages: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        if role not in {"system", "developer"}:
            content_messages.append(message)
            continue
        text = _message_content_to_text(message.get("content"))
        if not text:
            raise RuntimeError(
                "ChatGPT Codex onboarding generation received an empty instruction "
                "message"
            )
        instruction_parts.append(text)
    if not instruction_parts:
        raise RuntimeError(
            "ChatGPT Codex onboarding generation requires DSPy system instructions"
        )
    return "\n\n".join(instruction_parts), content_messages


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "ChatGPT Codex onboarding generation received unsupported "
                    "instruction content"
                )
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
                continue
            raise RuntimeError(
                "ChatGPT Codex onboarding generation received unsupported "
                f"instruction content type: {item_type}"
            )
        return "\n".join(parts)
    raise RuntimeError(
        "ChatGPT Codex onboarding generation received unsupported instruction "
        "content"
    )


def _stream_codex_response(
    *,
    model: str,
    messages: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> Any:
    request = _build_codex_responses_request(
        model=model,
        messages=messages,
        kwargs=kwargs,
    )
    model_name = request.pop("model")
    api_key = str(request.pop("api_key"))
    api_base = str(request.pop("api_base"))
    extra_headers = request.pop("extra_headers", None)
    client = OpenAI(
        api_key=api_key,
        base_url=api_base,
        default_headers=extra_headers,
    )
    with client.responses.stream(model=model_name, **request) as stream:
        return _collect_codex_stream_response(stream)


def _build_codex_responses_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    from dspy.clients.lm import _convert_chat_request_to_responses_request

    converted = _convert_chat_request_to_responses_request(
        dict(model=model, messages=messages, **kwargs)
    )
    raw_model = str(converted.pop("model"))
    provider, _, model_name = raw_model.partition("/")
    if provider != "openai" or not model_name:
        raise RuntimeError(f"Unsupported Codex DSPy model: {raw_model}")
    request = {
        "model": model_name,
        "input": converted["input"],
        "instructions": converted["instructions"],
        "store": bool(converted.get("store", False)),
        "api_key": converted["api_key"],
        "api_base": converted["api_base"],
    }
    extra_headers = converted.get("extra_headers")
    if extra_headers is not None:
        request["extra_headers"] = extra_headers
    return request


def _collect_codex_stream_response(stream: Any) -> Any:
    output_items: list[Any] = []
    for event in stream:
        if getattr(event, "type", None) == "response.output_item.done":
            item = getattr(event, "item", None)
            if item is not None:
                output_items.append(item)
    final_response = stream.get_final_response()
    if not output_items:
        return final_response
    return SimpleNamespace(
        output=output_items,
        usage=getattr(final_response, "usage", {}),
        model=getattr(final_response, "model", None),
    )


__all__ = [
    "build_dspy_lm",
    "import_dspy",
    "resolve_dspy_model_id",
]
