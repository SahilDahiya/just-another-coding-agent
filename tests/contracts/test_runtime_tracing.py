from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel

from just_another_coding_agent.runtime import stream_run_events
from just_another_coding_agent.runtime import stream_session_run_events
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps

AGENT_NAME_ATTRIBUTE = "gen_ai.agent.name"
TOOL_CALL_ID_ATTRIBUTE = "gen_ai.tool.call.id"
TOOL_NAME_ATTRIBUTE = "gen_ai.tool.name"


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

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attributes.update(attributes)

    def update_name(self, name: str) -> None:
        self.name = name

    def end(self) -> None:
        self.ended = True

    def is_recording(self) -> bool:
        return True


class _FakeUseSpan:
    def __init__(self, span: _FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        self.span.end()
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

    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
        **kwargs,
    ) -> _FakeUseSpan:
        del kwargs
        return _FakeUseSpan(self.start_span(name, attributes=attributes))


class _FakeTracerProvider:
    def __init__(self, tracer: _FakeTracer) -> None:
        self._tracer = tracer

    def get_tracer(self, _name: str, _version: str | None = None) -> _FakeTracer:
        return self._tracer


class _FakeHistogram:
    def record(
        self,
        _value: object,
        _attributes: dict[str, object] | None = None,
    ) -> None:
        return None


class _FakeMeter:
    def create_histogram(self, *args, **kwargs) -> _FakeHistogram:
        del args, kwargs
        return _FakeHistogram()


class _FakeMeterProvider:
    def get_meter(self, _name: str, _version: str | None = None) -> _FakeMeter:
        return _FakeMeter()


class _FakeLogger:
    pass


class _FakeLoggerProvider:
    def get_logger(self, _name: str, _version: str | None = None) -> _FakeLogger:
        return _FakeLogger()


async def test_stream_session_run_events_emits_pydanticai_agent_and_tool_spans(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    session_path = tmp_path / "session.jsonl"
    tracer = _FakeTracer()
    instrumentation = InstrumentationSettings(
        tracer_provider=_FakeTracerProvider(tracer),
        meter_provider=_FakeMeterProvider(),
        logger_provider=_FakeLoggerProvider(),
    )
    model = InstrumentedModel(
        FunctionModel(stream_function=make_write_stream()),
        instrumentation,
    )

    monkeypatch.setattr(
        "pydantic_ai.agent.use_span",
        lambda _span, **_kwargs: nullcontext(),
    )

    events = [
        event
        async for event in stream_session_run_events(
            model=model,
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

    agent_spans = [
        span
        for span in tracer.spans
        if span.attributes.get(AGENT_NAME_ATTRIBUTE) == "agent"
    ]
    tool_spans = [
        span
        for span in tracer.spans
        if span.attributes.get(TOOL_NAME_ATTRIBUTE) == "write"
    ]

    assert agent_spans
    assert tool_spans
    assert agent_spans[0].ended is True
    assert tool_spans[0].ended is True
    assert tool_spans[0].attributes[TOOL_CALL_ID_ATTRIBUTE] == "call-write"


async def test_stream_run_events_emits_jaca_run_model_and_tool_spans(
    monkeypatch,
) -> None:
    tracer = _FakeTracer()
    agent = Agent(
        FunctionModel(stream_function=make_write_stream()),
        output_type=str,
    )

    @agent.tool_plain
    async def write(path: str, content: str) -> str:
        return f"wrote {path}:{content}"

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.run._get_observability_tracer",
        lambda: tracer,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="write the note",
            available_tool_names=("write",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]

    run_spans = [span for span in tracer.spans if span.name == "jaca.run"]
    model_spans = [
        span for span in tracer.spans if span.name == "jaca.model_request"
    ]
    tool_spans = [span for span in tracer.spans if span.name == "jaca.tool"]

    assert len(run_spans) == 1
    assert len(model_spans) == 2
    assert len(tool_spans) == 1
    assert run_spans[0].ended is True
    assert tool_spans[0].ended is True
    assert tool_spans[0].attributes[TOOL_CALL_ID_ATTRIBUTE] == "call-write"
    assert tool_spans[0].attributes[TOOL_NAME_ATTRIBUTE] == "write"
    assert tool_spans[0].attributes["jaca.tool.status"] == "succeeded"
    assert [span.attributes["jaca.model_request.index"] for span in model_spans] == [
        1,
        2,
    ]


async def test_stream_run_events_binds_session_id_into_jaca_spans(
    monkeypatch,
    tmp_path,
) -> None:
    tracer = _FakeTracer()
    agent = Agent(
        FunctionModel(stream_function=make_write_stream()),
        output_type=str,
    )

    @agent.tool_plain
    async def write(path: str, content: str) -> str:
        return f"wrote {path}:{content}"

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.run._get_observability_tracer",
        lambda: tracer,
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="write the note",
            deps=WorkspaceDeps(
                workspace_root=tmp_path,
                session_scope=RunSessionScope(session_id="a" * 32),
            ),
            available_tool_names=("write",),
        )
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]

    traced_spans = [
        span
        for span in tracer.spans
        if span.name in {"jaca.run", "jaca.model_request", "jaca.tool"}
    ]

    assert traced_spans
    for span in traced_spans:
        assert span.attributes["jaca.session_id"] == "a" * 32
