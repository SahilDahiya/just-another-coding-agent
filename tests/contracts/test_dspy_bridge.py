from __future__ import annotations

from types import SimpleNamespace

import dspy

from just_another_coding_agent.runtime.dspy_bridge import (
    _build_codex_responses_request,
    _collect_codex_stream_response,
    build_dspy_lm,
    resolve_dspy_model_id,
)
from just_another_coding_agent.runtime.models import (
    OPENAI_CODEX_BASE_URL,
    resolve_canonical_model,
)


class _FakeDSPy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def LM(self, name: str, **kwargs: object) -> tuple[str, dict[str, object]]:
        self.calls.append((name, kwargs))
        return name, kwargs


def test_build_dspy_lm_uses_openai_api_key_lane(monkeypatch) -> None:
    fake = _FakeDSPy()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.compute_model_readiness",
        lambda _model_id: SimpleNamespace(
            provider="openai", configured=True, reason="ok"
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.resolve_provider_secret",
        lambda provider: "openai-key" if provider == "openai" else None,
    )

    lm = build_dspy_lm(dspy=fake, model="openai-responses:gpt-5.4")

    assert lm == (
        "openai/gpt-5.4",
        {
            "api_key": "openai-key",
            "api_base": "https://example.test/v1",
            "model_type": "responses",
        },
    )
    assert fake.calls == [
        (
            "openai/gpt-5.4",
            {
                "api_key": "openai-key",
                "api_base": "https://example.test/v1",
                "model_type": "responses",
            },
        )
    ]


def test_build_dspy_lm_uses_anthropic_lane(monkeypatch) -> None:
    fake = _FakeDSPy()
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.compute_model_readiness",
        lambda _model_id: SimpleNamespace(
            provider="anthropic",
            configured=True,
            reason="ok",
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.resolve_provider_secret",
        lambda provider: "anthropic-key" if provider == "anthropic" else None,
    )

    lm = build_dspy_lm(dspy=fake, model="anthropic:claude-sonnet-4-5")

    assert lm == ("anthropic/claude-sonnet-4-5", {"api_key": "anthropic-key"})


def test_build_dspy_lm_uses_chatgpt_subscription_lane(monkeypatch) -> None:
    fake = _FakeDSPy()
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.compute_model_readiness",
        lambda _model_id: SimpleNamespace(
            provider="openai", configured=True, reason="ok"
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-token", account_id="acct-123"),
    )

    lm = build_dspy_lm(dspy=fake, model="openai-responses:gpt-5.4-chatgpt")

    assert lm == (
        "openai/gpt-5.4",
        {
            "api_key": "oauth-token",
            "api_base": OPENAI_CODEX_BASE_URL,
            "extra_headers": {
                "chatgpt-account-id": "acct-123",
                "originator": "jaca",
                "OpenAI-Beta": "responses=experimental",
            },
            "model_type": "responses",
            "store": False,
        },
    )
    assert fake.calls == [
        (
            "openai/gpt-5.4",
            {
                "api_key": "oauth-token",
                "api_base": OPENAI_CODEX_BASE_URL,
                "extra_headers": {
                    "chatgpt-account-id": "acct-123",
                    "originator": "jaca",
                    "OpenAI-Beta": "responses=experimental",
                },
                "model_type": "responses",
                "store": False,
            },
        )
    ]


def test_build_dspy_lm_reuses_external_model_identity(monkeypatch) -> None:
    fake = _FakeDSPy()
    monkeypatch.setenv("OPENAI_API_KEY", "runtime-openai-key")
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.compute_model_readiness",
        lambda _model_id: SimpleNamespace(
            provider="openai", configured=True, reason="ok"
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.resolve_provider_secret",
        lambda provider: "openai-key" if provider == "openai" else None,
    )
    model = resolve_canonical_model("openai-responses:gpt-5.4")

    lm = build_dspy_lm(dspy=fake, model=model)

    assert lm == (
        "openai/gpt-5.4",
        {"api_key": "openai-key", "model_type": "responses"},
    )
    assert resolve_dspy_model_id(model) == "openai-responses:gpt-5.4"


def test_codex_oauth_lm_moves_system_prompt_to_instructions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        output: list[object] = []
        usage: dict[str, object] = {}
        model = "gpt-5.4"

    def fake_stream_codex_response(*, model, messages, kwargs):
        captured["model"] = model
        captured["messages"] = messages
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.compute_model_readiness",
        lambda _model_id: SimpleNamespace(
            provider="openai", configured=True, reason="ok"
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge.resolve_openai_codex_oauth_credentials_sync",
        lambda: SimpleNamespace(access="oauth-token", account_id="acct-123"),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.dspy_bridge._stream_codex_response",
        fake_stream_codex_response,
    )

    lm = build_dspy_lm(dspy=dspy, model="openai-responses:gpt-5.4-chatgpt")
    lm.forward(
        messages=[
            {"role": "system", "content": "You are a strict JSON generator."},
            {"role": "user", "content": "Generate one MCQ."},
        ],
        cache=False,
    )

    assert captured["model"] == "openai/gpt-5.4"
    assert captured["instructions"] == "You are a strict JSON generator."
    assert captured["store"] is False
    assert captured["messages"] == [
        {"role": "user", "content": "Generate one MCQ."}
    ]


def test_build_codex_responses_request_drops_token_fields() -> None:
    request = _build_codex_responses_request(
        model="openai/gpt-5.4",
        messages=[{"role": "user", "content": "Generate one MCQ."}],
        kwargs={
            "instructions": "You are a strict JSON generator.",
            "api_key": "oauth-token",
            "api_base": "https://chatgpt.com/backend-api/codex",
            "store": False,
            "max_tokens": 256,
        },
    )

    assert request["model"] == "gpt-5.4"
    assert request["instructions"] == "You are a strict JSON generator."
    assert request["store"] is False
    assert "input" in request
    assert "max_output_tokens" not in request
    assert "max_tokens" not in request


def test_collect_codex_stream_response_uses_output_item_done_events() -> None:
    output_item = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(text='{"greeting":"hello"}')],
    )

    class _FakeStream:
        def __iter__(self):
            yield SimpleNamespace(type="response.output_item.done", item=output_item)

        def get_final_response(self):
            return SimpleNamespace(
                output=[],
                usage={"total_tokens": 12},
                model="gpt-5.4",
            )

    response = _collect_codex_stream_response(_FakeStream())

    assert response.output == [output_item]
    assert response.usage == {"total_tokens": 12}
    assert response.model == "gpt-5.4"
