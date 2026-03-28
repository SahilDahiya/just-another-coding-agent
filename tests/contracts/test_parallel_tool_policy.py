from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager
from importlib import import_module

from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.runtime.agent import build_canonical_agent
from just_another_coding_agent.runtime.run import stream_run_events
from just_another_coding_agent.tools.deps import WorkspaceDeps

read_module = import_module("just_another_coding_agent.tools.read")
write_module = import_module("just_another_coding_agent.tools.write")


async def _parallel_reads_stream(
    messages,
    _agent_info,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="read",
                json_args='{"path":"a.txt"}',
                tool_call_id="call-read-a",
            ),
            1: DeltaToolCall(
                name="read",
                json_args='{"path":"b.txt"}',
                tool_call_id="call-read-b",
            ),
        }
        return

    yield "done"


async def _parallel_writes_stream(
    messages,
    _agent_info,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path":"a.txt","content":"a"}',
                tool_call_id="call-write-a",
            ),
            1: DeltaToolCall(
                name="write",
                json_args='{"path":"b.txt","content":"b"}',
                tool_call_id="call-write-b",
            ),
        }
        return

    yield "done"


async def _text_only_stream(messages, _agent_info) -> AsyncIterator[str]:
    assert len(messages) == 1
    yield "done"


async def test_stream_run_events_sets_parallel_tool_execution_mode_explicitly(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    modes: list[str] = []

    @contextmanager
    def _fake_execution_mode(mode: str = "parallel"):
        modes.append(mode)
        yield

    monkeypatch.setattr(
        Agent,
        "parallel_tool_call_execution_mode",
        staticmethod(_fake_execution_mode),
    )

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=_text_only_stream),
        workspace_root=workspace_root,
        tool_names=("read",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(workspace_root=workspace_root),
        )
    ]

    assert modes == ["parallel"]
    assert events[-1].type == "run_succeeded"


async def test_parallel_read_only_tool_calls_overlap(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _fake_execute_read(*, workspace_root, path, offset=None, limit=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return f"contents for {path}"

    monkeypatch.setattr(read_module, "execute_read", _fake_execute_read)

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=_parallel_reads_stream),
        workspace_root=workspace_root,
        tool_names=("read",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(workspace_root=workspace_root),
        )
    ]

    assert max_active == 2
    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]


async def test_mutating_tool_calls_remain_serialized(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _fake_execute_write(*, workspace_root, path, content):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return f"Wrote {workspace_root / path}"

    monkeypatch.setattr(write_module, "execute_write", _fake_execute_write)

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=_parallel_writes_stream),
        workspace_root=workspace_root,
        tool_names=("write",),
    )

    events = [
        event
        async for event in stream_run_events(
            agent=agent,
            prompt="go",
            deps=WorkspaceDeps(workspace_root=workspace_root),
        )
    ]

    assert max_active == 1
    assert [event.type for event in events] == [
        "run_started",
        "tool_call_started",
        "tool_call_started",
        "tool_call_succeeded",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
