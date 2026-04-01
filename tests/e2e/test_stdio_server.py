import io
import json
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.__main__ import main
from just_another_coding_agent.rpc import serve_rpc_stdio
from just_another_coding_agent.rpc.session_store import session_path_for_id
from just_another_coding_agent.session import load_session


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


async def compaction_summary_function(
    messages: list[ModelMessage],
    _agent_info: object,
) -> ModelResponse:
    prompt = _last_user_prompt(messages)
    assert prompt is not None
    assert "Prompt: create note" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps(
                    {
                        "current_objective": "finish note handling",
                        "established_facts": ["note.txt was created"],
                        "user_preferences": ["be concise"],
                        "important_paths": ["note.txt"],
                        "read_paths": [],
                        "modified_paths": ["note.txt"],
                        "open_questions": ["Should we add logging?"],
                        "unresolved_work": ["Run the final verifier."],
                    }
                )
            )
        ]
    )


async def test_serve_rpc_stdio_handles_multiple_lines_in_one_process(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "0" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
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
        model=FunctionModel(
            function=compaction_summary_function,
            stream_function=resume_aware_write_stream,
        ),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert messages[0] == {
        "type": "rpc_response",
        "id": "req-create",
        "response": {"session_id": fixed_session_id},
    }
    assert [message["type"] for message in messages[1:6]] == ["rpc_event"] * 5
    assert [message["event"]["type"] for message in messages[1:6]] == [
        "run_started",
        "tool_call_started",
        "tool_call_succeeded",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert [message["type"] for message in messages[6:]] == ["rpc_event"] * 3
    assert [message["event"]["type"] for message in messages[6:]] == [
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[-1]["event"]["output_text"] == "I created note.txt"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        session_id=fixed_session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]


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
        model=FunctionModel(
            function=compaction_summary_function,
            stream_function=resume_aware_write_stream,
        ),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = [
        json.loads(line) for line in output_stream.getvalue().splitlines() if line
    ]
    assert messages[0]["type"] == "rpc_response"
    assert messages[0]["id"] == "req-catalog"
    assert messages[0]["response"]["providers"][0]["provider"] == "ollama"
    assert (
        messages[0]["response"]["providers"][0]["default_model_id"]
        == "ollama:kimi-k2:1t-cloud"
    )
    assert messages[0]["response"]["providers"][1]["provider"] == "github"
    assert (
        messages[0]["response"]["providers"][1]["default_model_id"]
        == "github:openai/gpt-4.1"
    )


async def test_serve_rpc_stdio_supports_session_compact(
    tmp_path,
    monkeypatch,
) -> None:
    fixed_session_id = "0" * 32
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
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
        model=FunctionModel(
            function=compaction_summary_function,
            stream_function=resume_aware_write_stream,
        ),
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
    assert compact_response["response"]["first_kept_run_id"] is None
    assert (
        compact_response["response"]["summary"]["current_objective"]
        == "finish note handling"
    )
    assert compact_response["response"]["summary"]["important_paths"] == ["note.txt"]
    assert compact_response["response"]["summary"]["read_paths"] == []
    assert compact_response["response"]["summary"]["modified_paths"] == ["note.txt"]

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        session_id=fixed_session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert loaded.latest_compaction is not None
    assert (
        loaded.latest_compaction.compaction_id
        == compact_response["response"]["compaction_id"]
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
