import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

import just_another_coding_agent.rpc.state as rpc_state
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.rpc.stdio import handle_rpc_json_line
from just_another_coding_agent.session import load_session


@pytest.fixture(autouse=True, name="isolate_jaca_home")
def _isolate_jaca_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture(autouse=True, name="rpc_runtime_state")
def _rpc_runtime_state(monkeypatch):
    state = rpc_state._new_runtime_state()
    monkeypatch.setattr(rpc_state, "_RUNTIME_STATE", state)
    return state


isolate_jaca_home = _isolate_jaca_home
rpc_runtime_state = _rpc_runtime_state


async def noop_emit_queue_state(_event) -> None:
    return None


async def noop_emit_submitted_prompt_batch(_mode: str, _prompts: list[str]) -> None:
    return None


async def noop_emit_rpc_event(_request_id: str, _event) -> None:
    return None


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


async def looping_edit_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    if len(messages) >= 7:
        yield "done"
        return

    yield {
        0: DeltaToolCall(
            name="edit",
            json_args=(
                '{"path": "note.txt", "old_text": "missing", "new_text": "agent"}'
            ),
            tool_call_id=f"call-edit-{len(messages)}",
        )
    }


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    yield "done"


async def compaction_summary_stream(
    messages: list[ModelMessage],
    _agent_info: object,
) -> AsyncIterator[str]:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Runs since the latest compaction boundary:" in prompt
    assert "Primary intent:" in prompt
    assert "- create note" in prompt
    assert "Current state:" in prompt
    assert "Completed work:" in prompt
    assert "Tool evidence:" in prompt
    assert "create note" in prompt
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


async def exploding_session_stream(*_args, **_kwargs):
    raise RuntimeError("internal boom")
    yield  # pragma: no cover


async def rpc_messages(
    *,
    request_payload: object,
    model,
    workspace_root,
    sessions_root,
) -> list[dict[str, object]]:
    request_line = json.dumps(request_payload)

    async def _emit_rpc_event(_request_id: str, _event) -> None:
        return None

    return [
        json.loads(line)
        async for line in handle_rpc_json_line(
            line=request_line,
            model=model,
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=_emit_rpc_event,
        )
    ]


async def create_session_id(*, workspace_root, sessions_root) -> str:
    trust_messages = await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert trust_messages[0]["type"] == "rpc_response"
    assert trust_messages[0]["response"]["trusted"] is True

    messages = await rpc_messages(
        request_payload={
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages[0]["type"] == "rpc_response"
    assert messages[0]["id"] == "req-create"
    assert "project_docs" in messages[0]["response"]
    session_id = str(messages[0]["response"]["session_id"])
    assert len(session_id) == 32
    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
    )
    assert session_path.exists()
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.runs == []
    return session_id
