from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from just_another_coding_agent.runtime import observability
from just_another_coding_agent.runtime.local_traces import (
    LocalJSONLSpanExporter,
    build_local_trace_path,
)


def _fake_logfire_module(calls: list[dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        configure=lambda **kwargs: calls.append(kwargs),
        ScrubbingOptions=lambda **kwargs: {"_scrubbing": True, **kwargs},
    )


def test_configure_observability_configures_local_tracing_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setenv("JACA_TRACE_MODE", "local")
    monkeypatch.setattr(
        observability,
        "_configure_local_tracing",
        lambda service_name: calls.append(service_name),
    )
    monkeypatch.setattr(observability, "_configured", False)

    observability.configure_observability()

    assert calls == ["jaca"]



def test_configure_observability_fails_fast_without_logfire_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "logfire",
        _fake_logfire_module([]),
    )
    monkeypatch.setattr(observability, "_configured", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(
        RuntimeError,
        match="JACA_TRACE_MODE=logfire requires Logfire project credentials",
    ):
        observability.configure_observability()


def test_configure_observability_accepts_default_logfire_toml_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    logfire_dir = tmp_path / ".logfire"
    logfire_dir.mkdir()
    (logfire_dir / "default.toml").write_text(
        '\n'.join(
            [
                '[tokens."https://logfire-us.pydantic.dev"]',
                'token = "test-token"',
                'expiration = "2027-01-01T00:00:00Z"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("JACA_TRACE_MODE", "logfire")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "logfire",
        _fake_logfire_module(calls),
    )
    monkeypatch.setattr(observability, "_configured", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    observability.configure_observability()

    assert calls == [
        {
            "send_to_logfire": True,
            "console": False,
            "service_name": "jaca",
            "scrubbing": {
                "_scrubbing": True,
                "callback": observability._scrub_only_api_keys,
            },
        }
    ]


def test_build_local_trace_path_uses_jaca_trace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    path = build_local_trace_path(datetime(2026, 3, 28, 21, 5, 4))

    assert path.parent == tmp_path / ".jaca" / "traces" / "2026-03-28"
    assert path.name.startswith("trace-210504-")
    assert path.suffix == ".jsonl"


def test_local_jsonl_span_exporter_writes_jsonl_records(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    exporter = LocalJSONLSpanExporter(path)

    span = SimpleNamespace(
        name="tool_call",
        context=SimpleNamespace(trace_id=0x1234, span_id=0x5678),
        parent=SimpleNamespace(span_id=0x9999),
        start_time=10,
        end_time=20,
        attributes={"gen_ai.tool.name": "shell", "retries": 2},
        events=[
            SimpleNamespace(
                name="event",
                timestamp=15,
                attributes={"key": "value"},
            )
        ],
        status=SimpleNamespace(
            status_code=SimpleNamespace(name="OK"),
            description="done",
        ),
    )

    exporter.export([span])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["name"] == "tool_call"
    assert record["trace_id"] == "00000000000000000000000000001234"
    assert record["span_id"] == "0000000000005678"
    assert record["parent_span_id"] == "0000000000009999"
    assert record["attributes"]["gen_ai.tool.name"] == "shell"
    assert record["events"][0]["attributes"]["key"] == "value"
    assert record["status"] == {"code": "OK", "description": "done"}
