"""Run-level OpenTelemetry span management.

Helpers for creating and finishing spans that trace agent runs, model
requests, and tool calls.  Extracted from ``runtime.run`` to keep the
run loop focused on orchestration.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from typing import Any

from just_another_coding_agent.runtime.models import get_external_model_id
from just_another_coding_agent.runtime.observability import get_tracer

_RUN_SPAN_NAME = "jaca.run"
_MODEL_REQUEST_SPAN_NAME = "jaca.model_request"
_TOOL_SPAN_NAME = "jaca.tool"
_HARBOR_SPAN_ENV_KEYS = (
    ("JACA_HARBOR_JOB_NAME", "jaca.harbor.job_name"),
    ("JACA_HARBOR_SUBMISSION_ID", "jaca.harbor.submission_id"),
    ("JACA_HARBOR_SLICE_NAME", "jaca.harbor.slice_name"),
    ("TASK_NAME", "jaca.harbor.task_name"),
    ("HARBOR_TASK_NAME", "jaca.harbor.task_name"),
)


def _set_span_attributes(span: Any | None, attributes: dict[str, object]) -> None:
    if span is None:
        return
    if hasattr(span, "set_attributes"):
        span.set_attributes(attributes)
        return
    for key, value in attributes.items():
        span.set_attribute(key, value)


def _end_span(span: Any | None) -> None:
    if span is None:
        return
    span.end()


def _get_observability_tracer() -> Any | None:
    return get_tracer(__name__)


def _harbor_span_attributes_from_env() -> dict[str, str]:
    attributes: dict[str, str] = {}
    for env_key, attribute_key in _HARBOR_SPAN_ENV_KEYS:
        value = os.environ.get(env_key, "").strip()
        if value:
            attributes[attribute_key] = value
    return attributes


@contextlib.contextmanager
def _start_run_span(
    *,
    run_id: str,
    prompt: str,
    available_tool_names: Sequence[str],
    session_id: str | None,
) -> Any:
    tracer = _get_observability_tracer()
    if tracer is None:
        yield None
        return

    attributes: dict[str, object] = {
        "gen_ai.agent.name": "agent",
        "jaca.run.id": run_id,
        "jaca.run.prompt_chars": len(prompt),
        "jaca.run.tool_names": list(available_tool_names),
        "jaca.run.status": "running",
    }
    attributes.update(_harbor_span_attributes_from_env())
    if session_id is not None:
        attributes["jaca.session_id"] = session_id

    with tracer.start_as_current_span(
        _RUN_SPAN_NAME,
        attributes=attributes,
    ) as span:
        yield span


@contextlib.contextmanager
def _start_model_request_span(
    *,
    agent: Any,
    run_id: str,
    request_index: int,
    session_id: str | None,
) -> Any:
    tracer = _get_observability_tracer()
    if tracer is None:
        yield None
        return

    external_model_id = get_external_model_id(getattr(agent, "model", None))
    attributes: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "jaca.run.id": run_id,
        "jaca.model_request.index": request_index,
        "jaca.model_request.status": "running",
    }
    attributes.update(_harbor_span_attributes_from_env())
    if session_id is not None:
        attributes["jaca.session_id"] = session_id
    if external_model_id is not None:
        attributes["gen_ai.request.model"] = external_model_id

    with tracer.start_as_current_span(
        _MODEL_REQUEST_SPAN_NAME,
        attributes=attributes,
    ) as span:
        yield span


def _start_tool_span(
    *,
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    args_valid: bool | None,
    session_id: str | None,
) -> Any | None:
    tracer = _get_observability_tracer()
    if tracer is None:
        return None
    attributes: dict[str, object] = {
        "gen_ai.tool.call.id": tool_call_id,
        "gen_ai.tool.name": tool_name,
        "jaca.run.id": run_id,
        "jaca.tool.args_valid": args_valid
        if isinstance(args_valid, bool)
        else "unknown",
        "jaca.tool.status": "running",
    }
    attributes.update(_harbor_span_attributes_from_env())
    if session_id is not None:
        attributes["jaca.session_id"] = session_id
    return tracer.start_span(
        _TOOL_SPAN_NAME,
        attributes=attributes,
    )


def _finish_tool_span(
    *,
    active_tool_spans: dict[str, Any],
    tool_call_id: str,
    status: str,
    duration_ms: int | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    span = active_tool_spans.pop(tool_call_id, None)
    if span is None:
        return

    attributes: dict[str, object] = {"jaca.tool.status": status}
    if duration_ms is not None:
        attributes["jaca.tool.duration_ms"] = duration_ms
    if error_type is not None:
        attributes["jaca.tool.error_type"] = error_type
    if error_message is not None:
        attributes["jaca.tool.error_message"] = error_message
    _set_span_attributes(span, attributes)
    _end_span(span)


def _finish_all_tool_spans(
    *,
    active_tool_spans: dict[str, Any],
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    for tool_call_id in list(active_tool_spans):
        _finish_tool_span(
            active_tool_spans=active_tool_spans,
            tool_call_id=tool_call_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
        )


__all__ = [
    "_finish_all_tool_spans",
    "_finish_tool_span",
    "_get_observability_tracer",
    "_harbor_span_attributes_from_env",
    "_set_span_attributes",
    "_start_model_request_span",
    "_start_run_span",
    "_start_tool_span",
]
