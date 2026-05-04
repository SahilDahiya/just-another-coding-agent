import asyncio
import io
import json
import re
import sys
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.__main__ import main
from just_another_coding_agent.contracts.auth import ProviderAuthStatus
from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.runtime.turn_context import (
    build_runtime_context_message,
    build_runtime_context_update_message,
    build_runtime_context_update_text,
    build_session_turn_context_entry,
)
from just_another_coding_agent.runtime.workspace_trust import accept_workspace_trust
from just_another_coding_agent.session import load_session


@pytest.fixture(autouse=True)
def _default_stdio_trace_mode(monkeypatch) -> None:
    monkeypatch.setenv("JACA_TRACE_MODE", "off")


def _all_parts(messages: list[ModelMessage]):
    for message in messages:
        for part in message.parts:
            yield part


def _last_user_prompt(messages: list[ModelMessage]) -> str | None:
    prompt: str | None = None
    for part in _all_parts(messages):
        if isinstance(part, UserPromptPart):
            prompt = part.content
    return prompt


def _has_tool_return(messages: list[ModelMessage], *, tool_name: str) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        for part in _all_parts(messages)
    )


def _assistant_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, TextPart)
    ]


def _trust_workspace(workspace_root) -> None:
    accept_workspace_trust(workspace_root)


async def resume_aware_write_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    latest_prompt = _last_user_prompt(messages)
    saw_write = _has_tool_return(messages, tool_name="write")

    if latest_prompt == "create note" and not saw_write:
        yield {
            0: DeltaToolCall(
                name="write",
                json_args='{"path": "note.txt", "content": "hello\\n"}',
                tool_call_id="call-write",
            )
        }
        return

    if latest_prompt == "create note" and saw_write:
        yield "created"
        return

    if latest_prompt == "what did you do?":
        if not saw_write:
            raise AssertionError("missing prior message history")
        yield "I created note.txt"
        return

    raise AssertionError(f"unexpected prompt: {latest_prompt!r}")


async def compaction_summary_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Primary intent:" in prompt
    assert "- create note" in prompt
    assert "Current state:" in prompt
    assert "Completed work:" in prompt
    assert "Tool evidence:" in prompt
    yield "\n".join(
        [
            "Primary Intent:",
            "- Create note handling and preserve prior file work.",
            "Completed Work:",
            "- note.txt was created.",
            "Important Files/Paths:",
            "- note.txt: created during the previous run.",
            "Next Step:",
            "- Run the final verifier.",
            "Stable Preferences:",
            "- Be concise.",
        ]
    )


async def resume_or_compaction_stream(
    messages: list[ModelMessage],
    agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    prompt = _last_user_prompt(messages)
    if prompt is not None and "Runs since the latest compaction boundary:" in prompt:
        async for chunk in compaction_summary_stream(messages, agent_info):
            yield chunk
        return

    async for chunk in resume_aware_write_stream(messages, agent_info):
        yield chunk


async def test_serve_rpc_stdio_handles_multiple_lines_in_one_process(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "0" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _trust_workspace(workspace_root)
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-create",
                        "command": "session.create",
                        "payload": {},
                    }
                ),
                json.dumps(
                    {
                        "id": "req-1",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "create note",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-2",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "what did you do?",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.session_store.uuid4",
        lambda: SimpleNamespace(hex=fixed_session_id),
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert messages[0] == {
        "type": "rpc_response",
        "id": "req-create",
        "response": {"session_id": fixed_session_id, "project_docs": []},
    }
    assert [message["type"] for message in messages[1:7]] == ["rpc_event"] * 6
    assert [message["event"]["type"] for message in messages[1:7]] == [
        "session_turn_context_status",
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[1]["event"]["status"] == "missing"
    assert messages[1]["event"]["reason"] == "missing"
    assert messages[7] == {
        "type": "rpc_response",
        "id": "req-1",
        "response": {"session_id": fixed_session_id},
    }
    assert [message["type"] for message in messages[8:12]] == ["rpc_event"] * 4
    assert [message["event"]["type"] for message in messages[8:12]] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[8]["event"]["status"] == "reused"
    assert messages[8]["event"]["reason"] == "matched"
    assert messages[11]["event"]["output_text"] == "I created note.txt"
    assert messages[12] == {
        "type": "rpc_response",
        "id": "req-2",
        "response": {"session_id": fixed_session_id},
    }

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]


async def test_serve_rpc_stdio_handles_auth_status_while_run_is_streaming(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "1" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "workspace_root": str(workspace_root),
                "shell_family": "bash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "ship it",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-auth",
                        "command": "auth.status",
                        "payload": {},
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()

    async def fake_stream_session_run_events(**_kwargs):
        yield RunStartedEvent(run_id="run-stream")
        await asyncio.sleep(0)
        yield RunSucceededEvent(run_id="run-stream", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.list_provider_auth_statuses",
        lambda: [
            ProviderAuthStatus(
                provider="openai",
                configured=True,
                secret_configured=True,
                requires_secret=True,
                source="env",
                env_key="OPENAI_API_KEY",
                reason="ok",
            )
        ],
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    # Run-events and the auth-status response can interleave because they
    # come from concurrent process_line tasks; we only assert that both
    # streams are present and individually well-ordered.
    run_events = [
        m for m in messages if m["id"] == "req-run" and m["type"] == "rpc_event"
    ]
    run_responses = [
        m for m in messages if m["id"] == "req-run" and m["type"] == "rpc_response"
    ]
    auth_responses = [
        m for m in messages if m["id"] == "req-auth" and m["type"] == "rpc_response"
    ]
    assert [e["event"]["type"] for e in run_events] == [
        "run_started",
        "run_succeeded",
    ]
    assert run_responses == [
        {
            "type": "rpc_response",
            "id": "req-run",
            "response": {"session_id": fixed_session_id},
        }
    ]
    assert len(auth_responses) == 1
    assert auth_responses[0]["type"] == "rpc_response"


async def test_headless_auth_status_responds_without_waiting_for_second_line(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "just_another_coding_agent",
        "--headless",
        "--model",
        "openai-responses:gpt-5.4",
        "--workspace-root",
        str(workspace_root),
        "--sessions-root",
        str(sessions_root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    request = {
        "id": "req-auth",
        "command": "auth.status",
        "payload": {},
    }
    try:
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await process.stdin.drain()

        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        except asyncio.TimeoutError as error:
            process.terminate()
            _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            raise AssertionError(
                "headless auth.status did not respond after the first request line "
                f"within 15s; stderr={stderr.decode('utf-8', errors='replace')[:500]!r}"
            ) from error
        message = json.loads(line)
        assert message["type"] == "rpc_response"
        assert message["id"] == "req-auth"
        assert "providers" in message["response"]
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
            await process.stdin.wait_closed()
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.communicate(), timeout=5)


async def test_serve_rpc_stdio_drains_queued_follow_up_after_run(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "2" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "workspace_root": str(workspace_root),
                "shell_family": "bash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "first",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-enqueue",
                        "command": "run.enqueue",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "second",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()

    async def fake_stream_session_run_events(*, prompt: str, **_kwargs):
        yield RunStartedEvent(run_id=f"run-{prompt}")
        await asyncio.sleep(0.05)
        yield RunSucceededEvent(run_id=f"run-{prompt}", output_text=prompt)

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert [message["type"] for message in messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_response",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
    assert messages[0]["id"] == "req-run"
    assert messages[0]["event"]["type"] == "run_started"
    assert messages[0]["event"]["run_id"] == "run-first"
    assert messages[1]["event"]["type"] == "session_queue_state"
    assert messages[1]["event"]["later_prompts"] == ["second"]
    assert messages[2]["id"] == "req-enqueue"
    assert messages[3]["id"] == "req-run"
    assert messages[3]["event"]["type"] == "run_succeeded"
    assert messages[3]["event"]["run_id"] == "run-first"
    assert messages[4]["event"]["type"] == "session_queue_state"
    assert messages[4]["event"]["later_prompts"] == []
    assert messages[5]["event"]["type"] == "session_queued_prompt_batch_submitted"
    assert messages[5]["event"]["mode"] == "later"
    assert messages[5]["event"]["prompts"] == ["second"]
    assert messages[6]["id"] == "req-run"
    assert messages[6]["event"]["type"] == "run_started"
    assert messages[6]["event"]["run_id"] == "run-second"
    assert messages[7]["id"] == "req-run"
    assert messages[7]["event"]["type"] == "run_succeeded"
    assert messages[7]["event"]["run_id"] == "run-second"
    assert messages[8] == {
        "type": "rpc_response",
        "id": "req-run",
        "response": {"session_id": fixed_session_id},
    }


async def test_serve_rpc_stdio_batches_multiple_later_follow_ups_into_one_run(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "5" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "workspace_root": str(workspace_root),
                "shell_family": "bash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "first",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-enqueue-1",
                        "command": "run.enqueue",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "second",
                            "mode": "later",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-enqueue-2",
                        "command": "run.enqueue",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "third",
                            "mode": "later",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()
    observed_prompts: list[str] = []

    async def fake_stream_session_run_events(*, prompt: str, **_kwargs):
        observed_prompts.append(prompt)
        yield RunStartedEvent(run_id=f"run-{len(observed_prompts)}")
        await asyncio.sleep(0.05)
        yield RunSucceededEvent(
            run_id=f"run-{len(observed_prompts)}",
            output_text=prompt,
        )

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert [message["type"] for message in messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_response",
        "rpc_event",
        "rpc_response",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
    assert observed_prompts == ["first", "second\n\nthird"]
    assert messages[1]["event"]["type"] == "session_queue_state"
    assert messages[1]["event"]["later_prompts"] == ["second"]
    assert messages[2] == {
        "type": "rpc_response",
        "id": "req-enqueue-1",
        "response": {"session_id": fixed_session_id, "queued_count": 1},
    }
    assert messages[3]["event"]["type"] == "session_queue_state"
    assert messages[3]["event"]["later_prompts"] == ["second", "third"]
    assert messages[4] == {
        "type": "rpc_response",
        "id": "req-enqueue-2",
        "response": {"session_id": fixed_session_id, "queued_count": 2},
    }
    assert messages[5]["event"]["type"] == "run_succeeded"
    assert messages[5]["event"]["output_text"] == "first"
    assert messages[6]["event"]["type"] == "session_queue_state"
    assert messages[6]["event"]["later_prompts"] == []
    assert messages[7]["event"]["type"] == "session_queued_prompt_batch_submitted"
    assert messages[7]["event"]["mode"] == "later"
    assert messages[7]["event"]["prompts"] == ["second", "third"]
    assert messages[8]["event"]["type"] == "run_started"
    assert messages[9]["event"]["type"] == "run_succeeded"
    assert messages[9]["event"]["output_text"] == "second\n\nthird"
    assert messages[10] == {
        "type": "rpc_response",
        "id": "req-run",
        "response": {"session_id": fixed_session_id},
    }


async def test_serve_rpc_stdio_submits_next_queue_after_active_tool_phase_completes(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "3" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "workspace_root": str(workspace_root),
                "shell_family": "bash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "first",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-enqueue-next",
                        "command": "run.enqueue",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "be concise",
                            "mode": "next",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()
    attached_prompts: list[str] = []

    async def fake_stream_session_run_events(
        *,
        activate_steer_boundary,
        submit_steer_boundary,
        deactivate_steer_boundary,
        **_kwargs,
    ):
        def attach(prompts: list[str]) -> None:
            attached_prompts[:] = prompts

        yield RunStartedEvent(run_id="run-stream")
        await asyncio.sleep(0.05)
        await activate_steer_boundary(attach)
        assert attached_prompts == []
        await asyncio.sleep(0.05)
        await submit_steer_boundary()
        yield RunSucceededEvent(run_id="run-stream", output_text="done")
        await deactivate_steer_boundary()

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert messages[1]["event"]["type"] == "session_queue_state"
    assert messages[1]["event"]["next_prompts"] == ["be concise"]
    assert messages[2]["id"] == "req-enqueue-next"
    assert messages[2]["response"]["queued_count"] == 1
    assert messages[3]["event"]["type"] == "session_queue_state"
    assert messages[3]["event"]["next_prompts"] == []
    assert messages[3]["event"]["later_prompts"] == []
    assert messages[4]["event"]["type"] == "session_queued_prompt_batch_submitted"
    assert messages[4]["event"]["mode"] == "next"
    assert messages[4]["event"]["prompts"] == ["be concise"]
    assert attached_prompts == ["be concise"]


async def test_serve_rpc_stdio_interrupt_promotes_next_steer_into_immediate_follow_up(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "4" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_header",
                "workspace_root": str(workspace_root),
                "shell_family": "bash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "first",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-enqueue-next",
                        "command": "run.enqueue",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "second",
                            "mode": "next",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-interrupt",
                        "command": "run.interrupt",
                        "payload": {
                            "session_id": fixed_session_id,
                            "promote_queued_steer": True,
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()

    async def fake_stream_session_run_events(
        *,
        prompt: str,
        activate_steer_boundary,
        submit_steer_boundary,
        deactivate_steer_boundary,
        **_kwargs,
    ):
        def attach(_prompts: list[str]) -> None:
            return None

        yield RunStartedEvent(run_id=f"run-{prompt}")
        await activate_steer_boundary(attach)
        if prompt == "first":
            try:
                await asyncio.Event().wait()
            finally:
                await deactivate_steer_boundary()
        else:
            await submit_steer_boundary()
            await deactivate_steer_boundary()
            yield RunSucceededEvent(run_id=f"run-{prompt}", output_text=prompt)

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.stdio.stream_session_run_events",
        fake_stream_session_run_events,
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert [message["type"] for message in messages] == [
        "rpc_event",
        "rpc_event",
        "rpc_response",
        "rpc_event",
        "rpc_response",
        "rpc_event",
        "rpc_event",
        "rpc_event",
        "rpc_response",
    ]
    assert messages[0]["id"] == "req-run"
    assert messages[0]["event"]["type"] == "run_started"
    assert messages[0]["event"]["run_id"] == "run-first"
    assert messages[1]["event"]["type"] == "session_queue_state"
    assert messages[1]["event"]["next_prompts"] == ["second"]
    assert messages[2] == {
        "type": "rpc_response",
        "id": "req-enqueue-next",
        "response": {"session_id": fixed_session_id, "queued_count": 1},
    }
    assert messages[3]["event"]["type"] == "session_queue_state"
    assert messages[3]["event"]["next_prompts"] == []
    assert messages[3]["event"]["later_prompts"] == []
    assert messages[4] == {
        "type": "rpc_response",
        "id": "req-interrupt",
        "response": {"session_id": fixed_session_id, "promoted_count": 1},
    }
    assert messages[5]["event"]["type"] == "session_queued_prompt_batch_submitted"
    assert messages[5]["event"]["mode"] == "later"
    assert messages[5]["event"]["prompts"] == ["second"]
    assert messages[6]["id"] == "req-run"
    assert messages[6]["event"]["type"] == "run_started"
    assert messages[6]["event"]["run_id"] == "run-second"
    assert messages[7]["id"] == "req-run"
    assert messages[7]["event"]["type"] == "run_succeeded"
    assert messages[7]["event"]["run_id"] == "run-second"
    assert messages[7]["event"]["output_text"] == "second"
    assert messages[8] == {
        "type": "rpc_response",
        "id": "req-run",
        "response": {"session_id": fixed_session_id},
    }


async def first_turn_text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def test_serve_rpc_stdio_emits_model_and_thinking_runtime_context_update(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "1" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _trust_workspace(workspace_root)

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.session_store.uuid4",
        lambda: SimpleNamespace(hex=fixed_session_id),
    )

    first_input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-create",
                        "command": "session.create",
                        "payload": {},
                    }
                ),
                json.dumps(
                    {
                        "id": "req-first",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "first",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    first_output_stream = io.StringIO()

    await serve_rpc_stdio(
        input_stream=first_input_stream,
        output_stream=first_output_stream,
        model=FunctionModel(stream_function=first_turn_text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    observed: dict[str, object] = {}

    async def second_turn_probe_stream(
        messages: list[ModelMessage],
        _agent_info: object,
    ) -> AsyncIterator[str]:
        observed["assistant_texts"] = _assistant_texts(messages)
        observed["user_prompts"] = [
            part.content
            for part in _all_parts(messages)
            if isinstance(part, UserPromptPart)
        ]
        yield "done"

    second_input_stream = io.StringIO(
        json.dumps(
            {
                "id": "req-second",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "second",
                    "thinking": "high",
                },
            }
        )
        + "\n"
    )
    second_output_stream = io.StringIO()
    second_model = FunctionModel(stream_function=second_turn_probe_stream)

    await serve_rpc_stdio(
        input_stream=second_input_stream,
        output_stream=second_output_stream,
        model=second_model,
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line)
        for line in second_output_stream.getvalue().splitlines()
        if line
    ]
    assert [message["type"] for message in messages] == ["rpc_event"] * 4 + [
        "rpc_response"
    ]
    assert [message["event"]["type"] for message in messages[:-1]] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[0]["event"]["status"] == "cleared"
    assert messages[0]["event"]["reason"] == "model_mismatch"

    first_entry = build_session_turn_context_entry(
        run_id="run-1",
        model=FunctionModel(stream_function=first_turn_text_only_stream),
        workspace_root=workspace_root,
    )
    assert observed["assistant_texts"][0] == build_runtime_context_message(
        first_entry.runtime_context_text
    ).parts[0].content
    assert observed["assistant_texts"][-1] == build_runtime_context_update_message(
        build_runtime_context_update_text(
            entry=first_entry,
            model=second_model,
            workspace_root=workspace_root,
            thinking="high",
        )
    ).parts[0].content
    assert "done" in observed["assistant_texts"]
    assert observed["user_prompts"] == ["first", "second"]
    assert messages[-1] == {
        "type": "rpc_response",
        "id": "req-second",
        "response": {"session_id": fixed_session_id},
    }


async def test_serve_rpc_stdio_scopes_sessions_to_workspace_root(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "2" * 32
    first_workspace_root = tmp_path / "workspace-a"
    first_workspace_root.mkdir()
    second_workspace_root = tmp_path / "workspace-b"
    second_workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _trust_workspace(first_workspace_root)

    monkeypatch.setattr(
        "just_another_coding_agent.rpc.session_store.uuid4",
        lambda: SimpleNamespace(hex=fixed_session_id),
    )

    create_input_stream = io.StringIO(
        json.dumps(
            {
                "id": "req-create",
                "command": "session.create",
                "payload": {},
            }
        )
        + "\n"
    )
    create_output_stream = io.StringIO()

    await serve_rpc_stdio(
        input_stream=create_input_stream,
        output_stream=create_output_stream,
        model=FunctionModel(stream_function=first_turn_text_only_stream),
        workspace_root=first_workspace_root,
        sessions_root=sessions_root,
    )

    mismatch_input_stream = io.StringIO(
        json.dumps(
            {
                "id": "req-run",
                "command": "run.start",
                "payload": {
                    "session_id": fixed_session_id,
                    "prompt": "second",
                },
            }
        )
        + "\n"
    )
    mismatch_output_stream = io.StringIO()

    await serve_rpc_stdio(
        input_stream=mismatch_input_stream,
        output_stream=mismatch_output_stream,
        model=FunctionModel(stream_function=first_turn_text_only_stream),
        workspace_root=second_workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line)
        for line in mismatch_output_stream.getvalue().splitlines()
        if line
    ]
    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-run",
            "error_type": "UnknownSession",
            "message": f"Unknown session_id: {fixed_session_id}",
        }
    ]


async def test_serve_rpc_stdio_supports_model_catalog(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    input_stream = io.StringIO(
        json.dumps(
            {
                "id": "req-catalog",
                "command": "model.catalog",
                "payload": {},
            }
        )
        + "\n"
    )
    output_stream = io.StringIO()

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert messages[0]["type"] == "rpc_response"
    assert messages[0]["id"] == "req-catalog"
    assert messages[0]["response"]["providers"][0]["provider"] == "openai"
    assert (
        messages[0]["response"]["providers"][0]["default_model_id"]
        == "openai-responses:gpt-5.4"
    )
    assert messages[0]["response"]["providers"][1]["provider"] == "anthropic"
    assert (
        messages[0]["response"]["providers"][1]["default_model_id"]
        == "anthropic:claude-sonnet-4-5"
    )


async def test_serve_rpc_stdio_supports_session_compact(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "0" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _trust_workspace(workspace_root)
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-create",
                        "command": "session.create",
                        "payload": {},
                    }
                ),
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "create note",
                            "thinking": "high",
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "req-compact",
                        "command": "session.compact",
                        "payload": {"session_id": fixed_session_id},
                    }
                ),
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.session_store.uuid4",
        lambda: SimpleNamespace(hex=fixed_session_id),
    )

    await serve_rpc_stdio(
        input_stream=input_stream,
        output_stream=output_stream,
        model=FunctionModel(stream_function=resume_or_compaction_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    compact_response = messages[-1]

    assert compact_response["type"] == "rpc_response"
    assert compact_response["id"] == "req-compact"
    assert len(compact_response["response"]["compaction_id"]) == 32
    assert compact_response["response"]["compacted_through_run_id"]

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert (
        loaded.latest_compaction.compaction_id
        == compact_response["response"]["compaction_id"]
    )
    assert (
        loaded.latest_compaction.compacted_through_run_id
        == compact_response["response"]["compacted_through_run_id"]
    )


def test_main_parses_args_and_runs_stdio_server(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    input_stream = io.StringIO("")
    output_stream = io.StringIO()
    captured: dict[str, object] = {}
    call_order: list[str] = []

    async def fake_serve_rpc_stdio(**kwargs) -> None:
        call_order.append("serve")
        captured.update(kwargs)

    def fake_configure_observability() -> None:
        call_order.append("configure")

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.serve_rpc_stdio",
        fake_serve_rpc_stdio,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.__main__.configure_observability",
        fake_configure_observability,
    )

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--headless",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ],
        input_stream=input_stream,
        output_stream=output_stream,
    )

    assert exit_code == 0
    assert sessions_root.is_dir()
    assert call_order == ["configure", "serve"]
    assert captured == {
        "input_stream": input_stream,
        "output_stream": output_stream,
        "model": "openai:test-model",
        "workspace_root": workspace_root.resolve(),
        "sessions_root": sessions_root.resolve(),
    }


def test_main_headless_redirects_startup_stdout_to_stderr(
    tmp_path, monkeypatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    input_stream = io.StringIO("")
    stdout_stream = io.StringIO()
    stderr_stream = io.StringIO()

    async def fake_serve_rpc_stdio(**kwargs) -> None:
        kwargs["output_stream"].write(
            json.dumps(
                {
                    "type": "rpc_response",
                    "id": "1",
                    "response": {"providers": []},
                }
            )
            + "\n"
        )

    def fake_configure_observability() -> None:
        print("No Logfire project credentials found.")

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.serve_rpc_stdio",
        fake_serve_rpc_stdio,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.__main__.configure_observability",
        fake_configure_observability,
    )
    monkeypatch.setattr("sys.stdout", stdout_stream)
    monkeypatch.setattr("sys.stderr", stderr_stream)

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--headless",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ],
        input_stream=input_stream,
        output_stream=None,
    )

    assert exit_code == 0
    assert stdout_stream.getvalue() == (
        '{"type": "rpc_response", "id": "1", "response": {"providers": []}}\n'
    )
    assert "No Logfire project credentials found." in stderr_stream.getvalue()


def test_main_headless_redirects_serve_phase_stdout_to_stderr(
    tmp_path, monkeypatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    input_stream = io.StringIO("")
    stdout_stream = io.StringIO()
    stderr_stream = io.StringIO()

    async def fake_serve_rpc_stdio(**kwargs) -> None:
        print("request-phase stdout noise")
        kwargs["output_stream"].write(
            json.dumps(
                {
                    "type": "rpc_response",
                    "id": "1",
                    "response": {"providers": []},
                }
            )
            + "\n"
        )

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.serve_rpc_stdio",
        fake_serve_rpc_stdio,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.__main__.flush_observability",
        lambda: print("flush-phase stdout noise"),
    )
    monkeypatch.setattr("sys.stdout", stdout_stream)
    monkeypatch.setattr("sys.stderr", stderr_stream)

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--headless",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ],
        input_stream=input_stream,
        output_stream=None,
    )

    assert exit_code == 0
    assert stdout_stream.getvalue() == (
        '{"type": "rpc_response", "id": "1", "response": {"providers": []}}\n'
    )
    assert "request-phase stdout noise" in stderr_stream.getvalue()
    assert "flush-phase stdout noise" in stderr_stream.getvalue()


def test_main_fails_fast_when_workspace_root_is_missing(tmp_path) -> None:
    missing_workspace_root = tmp_path / "missing-workspace"
    sessions_root = tmp_path / "sessions"

    with pytest.raises(
        FileNotFoundError,
        match=re.escape(
            f"Workspace root does not exist: {missing_workspace_root.resolve()}"
        ),
    ):
        main(
            [
                "--model",
                "openai:test-model",
                "--workspace-root",
                str(missing_workspace_root),
                "--sessions-root",
                str(sessions_root),
            ]
        )


def test_main_fails_fast_when_sessions_root_is_a_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions-file"
    sessions_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        NotADirectoryError,
        match=re.escape(f"Sessions root is not a directory: {sessions_root.resolve()}"),
    ):
        main(
            [
                "--model",
                "openai:test-model",
                "--workspace-root",
                str(workspace_root),
                "--sessions-root",
                str(sessions_root),
            ]
        )


def test_main_exits_cleanly_on_keyboard_interrupt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    def fake_asyncio_run(awaitable) -> None:
        awaitable.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.asyncio.run",
        fake_asyncio_run,
    )
    monkeypatch.setattr("just_another_coding_agent.__main__.load_config", lambda: {})

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--headless",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 130


async def test_serve_rpc_stdio_keeps_event_loop_live_under_slow_output_stream(
    tmp_path,
    monkeypatch,
) -> None:
    """Regression guard for commit 38e60ca ("Move RPC writes off the event
    loop and synthesize terminal events on early exit").

    The original bug was that `serve_rpc_stdio` wrote RPC envelopes to
    `output_stream` synchronously from an asyncio task. When the consumer
    (notably the small ~4 KB Windows anonymous pipe to the Go TUI) was
    slow to drain, the sync write blocked the entire event loop, which
    in turn deadlocked pydantic-ai's internal `asyncio.Event.wait()`
    ping-pong in `_streaming_handler` / `_do_run` because those
    primitives depend on the outer loop making progress.

    The fix routes all writes through an unbounded `asyncio.Queue` + a
    single dedicated writer task that hands the actual sync pipe write
    off to `asyncio.to_thread(...)`. A full or slow pipe can only block
    the writer thread, never the event loop.

    This test proves the loop stays live by running a tiny 10 ms-tick
    liveness counter concurrently with a `serve_rpc_stdio` call whose
    output stream deliberately sleeps 80 ms in each `write`. If the
    writes ever move back onto the event loop, the counter will advance
    far less than expected and the assertion will fail.
    """
    fixed_session_id = "0" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    _trust_workspace(workspace_root)
    monkeypatch.setattr(
        "just_another_coding_agent.rpc.session_store.uuid4",
        lambda: SimpleNamespace(hex=fixed_session_id),
    )

    class SlowOutputStream:
        """TextIO-like that sleeps for 80 ms per write call.

        The writer task calls `output_stream.write(...)` + `.flush()`
        inside `asyncio.to_thread(...)`, so the sleep runs in the
        default executor thread pool, not the asyncio event loop. The
        event loop is therefore free to run other tasks during the
        sleep — which is exactly what this test verifies via the
        liveness counter.
        """

        def __init__(self) -> None:
            self._buffer = io.StringIO()

        def write(self, data: str) -> int:
            time.sleep(0.08)
            self._buffer.write(data)
            return len(data)

        def flush(self) -> None:
            pass

        def getvalue(self) -> str:
            return self._buffer.getvalue()

    output_stream = SlowOutputStream()

    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "req-create",
                        "command": "session.create",
                        "payload": {},
                    }
                ),
                json.dumps(
                    {
                        "id": "req-run",
                        "command": "run.start",
                        "payload": {
                            "session_id": fixed_session_id,
                            "prompt": "create note",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )

    liveness_counter = {"count": 0}
    stop_flag = asyncio.Event()

    async def liveness_task() -> None:
        while not stop_flag.is_set():
            try:
                await asyncio.wait_for(stop_flag.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                pass
            liveness_counter["count"] += 1

    counter = asyncio.create_task(liveness_task())

    try:
        await serve_rpc_stdio(
            input_stream=input_stream,
            output_stream=output_stream,
            model=FunctionModel(stream_function=resume_or_compaction_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
        )
    finally:
        stop_flag.set()
        await counter

    # Sanity check: the slow writer actually produced output for both
    # requests, i.e. the pipeline ran end-to-end.
    output_value = output_stream.getvalue()
    assert "req-create" in output_value
    assert "req-run" in output_value

    # The liveness counter must have advanced significantly during
    # serve_rpc_stdio. The run emits several RPC envelopes, each of
    # which costs ~80 ms of blocking write time in the worker thread.
    # With a 10 ms-tick liveness counter, even a modest number of
    # writes (~5) should leave the counter with dozens of ticks.
    #
    # If the writer task ever moves back to synchronous writes on the
    # event loop, the counter will barely advance (it only gets to
    # run between writes, which is essentially zero time), and this
    # assertion will fail. 10 is a conservative floor that survives
    # heavy CI scheduler jitter.
    assert liveness_counter["count"] >= 10, (
        f"event loop advanced only {liveness_counter['count']} ticks during "
        f"serve_rpc_stdio with a slow output stream; the RPC writer may be "
        f"blocking the event loop instead of offloading to a worker thread"
    )
