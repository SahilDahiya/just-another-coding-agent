"""Runtime package for coding-agent orchestration."""

from .agent import (
    CANONICAL_AGENT_INSTRUCTIONS,
    build_canonical_agent,
    build_canonical_instructions,
    build_canonical_model_settings,
)
from .models import resolve_canonical_model
from .recovery import CANONICAL_RUN_RECOVERY_RETRY_LIMIT
from .run import stream_run_events
from .session import stream_session_run_events

__all__ = [
    "CANONICAL_AGENT_INSTRUCTIONS",
    "CANONICAL_RUN_RECOVERY_RETRY_LIMIT",
    "build_canonical_agent",
    "build_canonical_instructions",
    "build_canonical_model_settings",
    "resolve_canonical_model",
    "stream_run_events",
    "stream_session_run_events",
]
