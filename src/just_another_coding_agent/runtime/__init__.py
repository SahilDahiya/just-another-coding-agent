"""Runtime package for coding-agent orchestration."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CANONICAL_AGENT_OUTPUT_RETRIES",
    "CANONICAL_AGENT_TOOL_CORRECTION_RETRIES",
    "CANONICAL_AGENT_INSTRUCTIONS",
    "CANONICAL_RUN_RECOVERY_RETRY_LIMIT",
    "build_canonical_agent",
    "build_canonical_instructions",
    "build_runtime_context_text",
    "build_static_agent_instructions",
    "build_canonical_model_settings",
    "resolve_canonical_model",
    "stream_run_events",
    "stream_session_run_events",
]

_LAZY_EXPORTS = {
    "CANONICAL_AGENT_OUTPUT_RETRIES": (".agent", "CANONICAL_AGENT_OUTPUT_RETRIES"),
    "CANONICAL_AGENT_TOOL_CORRECTION_RETRIES": (
        ".agent",
        "CANONICAL_AGENT_TOOL_CORRECTION_RETRIES",
    ),
    "CANONICAL_AGENT_INSTRUCTIONS": (".agent", "CANONICAL_AGENT_INSTRUCTIONS"),
    "build_canonical_agent": (".agent", "build_canonical_agent"),
    "build_canonical_instructions": (".agent", "build_canonical_instructions"),
    "build_runtime_context_text": (".agent", "build_runtime_context_text"),
    "build_static_agent_instructions": (".agent", "build_static_agent_instructions"),
    "build_canonical_model_settings": (
        ".models",
        "build_canonical_model_settings",
    ),
    "resolve_canonical_model": (".models", "resolve_canonical_model"),
    "CANONICAL_RUN_RECOVERY_RETRY_LIMIT": (
        ".recovery",
        "CANONICAL_RUN_RECOVERY_RETRY_LIMIT",
    ),
    "stream_run_events": (".run", "stream_run_events"),
    "stream_session_run_events": (".session", "stream_session_run_events"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as error:  # pragma: no cover - standard module protocol
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from error

    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
