from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.trace import Span

RUN_SPAN_NAME = "jaca.run"
TOOL_SPAN_NAME = "jaca.tool_call"

RUN_ID_ATTRIBUTE = "jaca.run.id"
RUN_STATUS_ATTRIBUTE = "jaca.run.status"
TOOL_CALL_ID_ATTRIBUTE = "jaca.tool_call.id"
TOOL_NAME_ATTRIBUTE = "jaca.tool.name"
TOOL_STATUS_ATTRIBUTE = "jaca.tool.status"


@dataclass
class RuntimeTraceRecorder:
    run_id: str
    enabled: bool = field(default=False, init=False)
    _run_span: Span | None = field(default=None, init=False, repr=False)
    _run_scope: contextlib.AbstractContextManager[Span] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _tool_spans: dict[str, Span] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not _env_flag("JACA_TRACE"):
            return

        tracer = trace.get_tracer("just_another_coding_agent.runtime")
        run_span = tracer.start_span(
            RUN_SPAN_NAME,
            attributes={
                RUN_ID_ATTRIBUTE: self.run_id,
            },
        )
        self.enabled = True
        self._run_span = run_span
        self._run_scope = trace.use_span(run_span, end_on_exit=False)
        self._run_scope.__enter__()

    def start_tool(self, *, tool_call_id: str, tool_name: str) -> None:
        if not self.enabled or self._run_span is None:
            return
        if tool_call_id in self._tool_spans:
            raise RuntimeError(f"Tool span already started: {tool_call_id}")

        tracer = trace.get_tracer("just_another_coding_agent.runtime")
        self._tool_spans[tool_call_id] = tracer.start_span(
            TOOL_SPAN_NAME,
            attributes={
                RUN_ID_ATTRIBUTE: self.run_id,
                TOOL_CALL_ID_ATTRIBUTE: tool_call_id,
                TOOL_NAME_ATTRIBUTE: tool_name,
            },
            context=trace.set_span_in_context(self._run_span),
        )

    def finish_tool(
        self,
        *,
        tool_call_id: str,
        status: str,
    ) -> None:
        if not self.enabled:
            return

        span = self._tool_spans.pop(tool_call_id, None)
        if span is None:
            raise RuntimeError(f"Tool span not started: {tool_call_id}")

        span.set_attribute(TOOL_STATUS_ATTRIBUTE, status)
        span.end()

    def finish_run(self, *, status: str) -> None:
        if not self.enabled:
            return
        if self._run_span is None:
            raise RuntimeError("Run span not started")

        for tool_call_id in list(self._tool_spans):
            self.finish_tool(tool_call_id=tool_call_id, status="cancelled")

        self._run_span.set_attribute(RUN_STATUS_ATTRIBUTE, status)
        self._run_span.end()
        if self._run_scope is not None:
            self._run_scope.__exit__(None, None, None)

        self.enabled = False
        self._run_span = None
        self._run_scope = None


def _env_flag(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


__all__ = [
    "RUN_ID_ATTRIBUTE",
    "RUN_SPAN_NAME",
    "RUN_STATUS_ATTRIBUTE",
    "RuntimeTraceRecorder",
    "TOOL_CALL_ID_ATTRIBUTE",
    "TOOL_NAME_ATTRIBUTE",
    "TOOL_SPAN_NAME",
    "TOOL_STATUS_ATTRIBUTE",
]
