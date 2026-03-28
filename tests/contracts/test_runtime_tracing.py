from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.runtime.tracing import (
    RUN_SPAN_NAME,
    RUN_STATUS_ATTRIBUTE,
    TOOL_NAME_ATTRIBUTE,
    TOOL_SPAN_NAME,
    TOOL_STATUS_ATTRIBUTE,
)


def make_write_stream():
    call_count = 0

    async def write_stream(
        _messages: object,
        _agent_info: object,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args='{"path": "note.txt", "content": "hello\\n"}',
                    tool_call_id="call-write",
                )
            }
            return

        yield "done"

    return write_stream


@dataclass
class _FakeSpan:
    name: str
    attributes: dict[str, object]
    ended: bool = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class _FakeUseSpan:
    def __init__(self, span: _FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_span(
        self,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
        context: object | None = None,
    ) -> _FakeSpan:
        del context
        span = _FakeSpan(name=name, attributes=dict(attributes or {}))
        self.spans.append(span)
        return span


async def test_stream_session_run_events_emits_runtime_run_and_tool_spans(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    tracer = _FakeTracer()

    monkeypatch.setenv("JACA_TRACE", "1")
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.tracing.trace.get_tracer",
        lambda _name: tracer,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.runtime.tracing.trace.use_span",
        lambda span, **_kwargs: _FakeUseSpan(span),
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=FunctionModel(stream_function=make_write_stream()),
            workspace_root=workspace_root,
            session_path=session_path,
            prompt="write the note",
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]

    run_spans = [span for span in tracer.spans if span.name == RUN_SPAN_NAME]
    tool_spans = [span for span in tracer.spans if span.name == TOOL_SPAN_NAME]

    assert len(run_spans) == 1
    assert len(tool_spans) == 1

    run_span = run_spans[0]
    tool_span = tool_spans[0]
    assert run_span.ended is True
    assert tool_span.ended is True
    assert run_span.attributes[RUN_STATUS_ATTRIBUTE] == "succeeded"
    assert tool_span.attributes[TOOL_NAME_ATTRIBUTE] == "write"
    assert tool_span.attributes[TOOL_STATUS_ATTRIBUTE] == "succeeded"
    assert "jaca.run.id" in run_span.attributes
