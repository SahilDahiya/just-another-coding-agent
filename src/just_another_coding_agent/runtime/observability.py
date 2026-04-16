from __future__ import annotations

import importlib
import os
import shutil
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from just_another_coding_agent.runtime.env import trace_mode
from just_another_coding_agent.runtime.local_traces import (
    LocalJSONLSpanExporter,
    build_local_trace_path,
)

_configured = False
_DEFAULT_SERVICE_NAME = "jaca"
_TRACEPARENT_ENV_VAR = "JACA_TRACEPARENT"
_TRACESTATE_ENV_VAR = "JACA_TRACESTATE"


@dataclass(frozen=True)
class LogfireSetupStatus:
    installed: bool
    credentials_configured: bool


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
        if not _has_logfire_credentials():
            raise RuntimeError(
                "JACA_TRACE_MODE=logfire requires Logfire project credentials. "
                "Run `logfire auth` and `logfire projects use "
                "<project>` or set `LOGFIRE_TOKEN`."
            )
        # Scrubbing is disabled so trace attributes are sent exactly as
        # emitted.  Any new span attribute must NOT carry secrets (API keys,
        # tokens, passwords).  See _start_run_span / _start_model_request_span
        # / _start_tool_span in runtime/run.py and _trace_exec_prompt_run in
        # evaluations/bench/exec_prompt.py for the current attribute surface.
        logfire.configure(
            send_to_logfire=True,
            console=False,
            service_name=service_name,
            scrubbing=False,
        )
    else:
        raise AssertionError(f"unsupported trace mode: {mode}")

    _configured = True


def flush_observability(*, timeout_millis: int = 5000) -> None:
    if not _configured:
        return
    if trace_mode() != "logfire":
        return
    logfire = _import_logfire()
    logfire.force_flush(timeout_millis=timeout_millis)


def export_trace_context_env() -> dict[str, str]:
    if trace_mode() == "off":
        return {}

    if trace_mode() == "logfire":
        logfire = _import_logfire()
        carrier = dict(logfire.get_context())
    else:
        inject = _import_otel_propagate_inject()
        carrier: dict[str, str] = {}
        inject(carrier)
    traceparent = carrier.get("traceparent", "").strip()
    if not traceparent:
        return {}

    env = {_TRACEPARENT_ENV_VAR: traceparent}
    tracestate = carrier.get("tracestate", "").strip()
    if tracestate:
        env[_TRACESTATE_ENV_VAR] = tracestate
    return env


@contextmanager
def use_inherited_trace_context() -> Iterator[None]:
    if trace_mode() == "off":
        yield
        return

    traceparent = os.environ.get(_TRACEPARENT_ENV_VAR, "").strip()
    if not traceparent:
        yield
        return

    carrier = {"traceparent": traceparent}
    tracestate = os.environ.get(_TRACESTATE_ENV_VAR, "").strip()
    if tracestate:
        carrier["tracestate"] = tracestate

    if trace_mode() == "logfire":
        logfire = _import_logfire()
        with logfire.attach_context(carrier):
            yield
        return

    extract = _import_otel_propagate_extract()
    attach, detach = _import_otel_context_attach_detach()
    token = attach(extract(carrier))
    try:
        yield
    finally:
        detach(token)


def get_tracer(name: str) -> Any | None:
    if trace_mode() == "off":
        return None
    otel_get_tracer = _import_otel_trace_get_tracer()
    return otel_get_tracer(name)


def _import_logfire() -> Any:
    try:
        return importlib.import_module("logfire")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "JACA_TRACE_MODE=logfire requires the `logfire` package in this "
            "environment. Install it with `pip install logfire`, run "
            "`logfire auth`, and run `logfire projects use <project>` or set "
            "`LOGFIRE_TOKEN`."
        ) from error


def _import_otel_trace_get_tracer() -> Any:
    try:
        from opentelemetry import trace
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Tracing requires the `opentelemetry-api` package in this environment."
        ) from error
    return trace.get_tracer


def _import_otel_propagate_inject() -> Any:
    try:
        from opentelemetry.propagate import inject
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Tracing requires the `opentelemetry-api` package in this environment."
        ) from error
    return inject


def _import_otel_propagate_extract() -> Any:
    try:
        from opentelemetry.propagate import extract
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Tracing requires the `opentelemetry-api` package in this environment."
        ) from error
    return extract


def _import_otel_context_attach_detach() -> tuple[Any, Any]:
    try:
        from opentelemetry.context import attach, detach
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Tracing requires the `opentelemetry-api` package in this environment."
        ) from error
    return attach, detach


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


def logfire_setup_status() -> LogfireSetupStatus:
    return LogfireSetupStatus(
        installed=_is_logfire_installed(),
        credentials_configured=_has_logfire_credentials(),
    )


def _is_logfire_installed() -> bool:
    try:
        importlib.import_module("logfire")
    except ModuleNotFoundError:
        return False
    return shutil.which("logfire") is not None


__all__ = [
    "LogfireSetupStatus",
    "configure_observability",
    "export_trace_context_env",
    "flush_observability",
    "get_tracer",
    "logfire_setup_status",
    "use_inherited_trace_context",
]
