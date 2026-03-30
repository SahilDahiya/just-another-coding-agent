from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from opentelemetry.sdk.trace.export import SpanExportResult
except ModuleNotFoundError:  # pragma: no cover - exercised in no-trace installs
    class _SpanExportResult:
        SUCCESS = "success"

    SpanExportResult = _SpanExportResult


def build_local_trace_path(now: datetime | None = None) -> Path:
    timestamp = datetime.now() if now is None else now
    trace_dir = (
        Path.home()
        / ".jaca"
        / "traces"
        / timestamp.strftime("%Y-%m-%d")
    )
    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir / (
        f"trace-{timestamp.strftime('%H%M%S-%f')}-{os.getpid()}.jsonl"
    )


class LocalJSONLSpanExporter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def export(self, spans: list[Any]) -> Any:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                for span in spans:
                    handle.write(json.dumps(_serialize_span(span), sort_keys=True))
                    handle.write("\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def _serialize_span(span: Any) -> dict[str, Any]:
    context = span.context
    parent = span.parent
    return {
        "name": span.name,
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
        "parent_span_id": (
            None if parent is None else f"{parent.span_id:016x}"
        ),
        "start_time_unix_nano": span.start_time,
        "end_time_unix_nano": span.end_time,
        "attributes": _normalize_attributes(getattr(span, "attributes", {})),
        "events": [
            {
                "name": event.name,
                "timestamp_unix_nano": event.timestamp,
                "attributes": _normalize_attributes(
                    getattr(event, "attributes", {})
                ),
            }
            for event in getattr(span, "events", [])
        ],
        "status": _serialize_status(getattr(span, "status", None)),
    }


def _normalize_attributes(attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, dict):
        return {}
    return {
        str(key): _normalize_attribute_value(value)
        for key, value in attributes.items()
    }


def _normalize_attribute_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_normalize_attribute_value(item) for item in value]
    return str(value)


def _serialize_status(status: Any) -> dict[str, Any] | None:
    if status is None:
        return None
    code = getattr(status, "status_code", None)
    return {
        "code": None if code is None else getattr(code, "name", str(code)),
        "description": getattr(status, "description", None),
    }


__all__ = ["LocalJSONLSpanExporter", "build_local_trace_path"]
