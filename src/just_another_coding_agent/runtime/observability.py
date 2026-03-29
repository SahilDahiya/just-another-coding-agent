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

    logfire = _import_logfire()
    kwargs: dict[str, Any] = {
        "console": False,
        "service_name": os.environ.get(
            "LOGFIRE_SERVICE_NAME",
            _DEFAULT_SERVICE_NAME,
        ),
    }
    if mode == "local":
        kwargs["send_to_logfire"] = False
        kwargs["additional_span_processors"] = _build_local_span_processors()
    elif mode == "logfire":
        if not _has_logfire_credentials():
            raise RuntimeError(
                "JACA_TRACE_MODE=logfire requires Logfire project credentials. "
                "Run `uv run logfire auth` and `uv run logfire projects use "
                "<project>` or set `LOGFIRE_TOKEN`."
            )
        kwargs["send_to_logfire"] = True
    else:
        raise AssertionError(f"unsupported trace mode: {mode}")

    logfire.configure(**kwargs)
    _configured = True


def _import_logfire() -> Any:
    try:
        import logfire
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Tracing requires the optional `logfire` dependency. Install it "
            "with `uv sync --extra trace` and try again."
        ) from error
    return logfire


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
