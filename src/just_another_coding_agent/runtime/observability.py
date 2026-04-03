from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from just_another_coding_agent.runtime.env import trace_mode
from just_another_coding_agent.runtime.local_traces import (
    LocalJSONLSpanExporter,
    build_local_trace_path,
)

_configured = False
_DEFAULT_SERVICE_NAME = "jaca"


def configure_observability() -> None:
    global _configured

    mode = trace_mode()
    if mode == "off":
        return
    if _configured:
        return

    service_name = os.environ.get("LOGFIRE_SERVICE_NAME", _DEFAULT_SERVICE_NAME)
    if mode == "local":
        _configure_local_tracing(service_name)
    elif mode == "logfire":
        logfire = _import_logfire()
        scrubbing = logfire.ScrubbingOptions(callback=_scrub_only_api_keys)
        if not _has_logfire_credentials():
            raise RuntimeError(
                "JACA_TRACE_MODE=logfire requires Logfire project credentials. "
                "Run `uv run logfire auth` and `uv run logfire projects use "
                "<project>` or set `LOGFIRE_TOKEN`."
            )
        logfire.configure(
            send_to_logfire=True,
            console=False,
            service_name=service_name,
            scrubbing=scrubbing,
        )
    else:
        raise AssertionError(f"unsupported trace mode: {mode}")

    _configured = True


_API_KEY_PATTERNS = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "secret",
        "secret_key",
        "token",
        "password",
        "passwd",
        "authorization",
        "openai_api_key",
        "anthropic_api_key",
        "ollama_api_key",
        "google_api_key",
        "logfire_token",
    }
)


def _scrub_only_api_keys(match: Any) -> Any:
    path = match.path
    if isinstance(path, str):
        key = path.rsplit(".", 1)[-1].lower().replace("-", "_")
        if key in _API_KEY_PATTERNS:
            return "[Redacted]"
    return None


def _import_logfire() -> Any:
    import logfire

    return logfire


def _configure_local_tracing(service_name: str) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    for processor in _build_local_span_processors():
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


def _build_local_span_processors() -> list[Any]:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    trace_path = build_local_trace_path()
    return [SimpleSpanProcessor(LocalJSONLSpanExporter(trace_path))]


def _has_logfire_credentials() -> bool:
    if os.environ.get("LOGFIRE_TOKEN", "").strip():
        return True

    config_path = Path.home() / ".logfire" / "default.toml"
    if not config_path.exists():
        return False

    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(
            f"Invalid Logfire credentials file: {config_path}"
        ) from error

    tokens = config.get("tokens")
    if not isinstance(tokens, dict):
        return False

    for value in tokens.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict):
            token = value.get("token")
            if isinstance(token, str) and token.strip():
                return True

    return False


__all__ = ["configure_observability"]
