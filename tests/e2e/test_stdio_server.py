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
from just_another_coding_agent.runtime.turn_context import (
    build_runtime_context_message,
    build_runtime_context_update_message,
    build_runtime_context_update_text,
    build_session_turn_context_entry,
)
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


def _assistant_texts(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for part in _all_parts(messages)
        if isinstance(part, TextPart)
    ]


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
    assert "Primary intent:" in prompt
    assert "- create note" in prompt
    assert "Current state:" in prompt
    assert "Completed work:" in prompt
    assert "Tool evidence:" in prompt
    return ModelResponse(
        parts=[
            TextPart(
                content="\n".join(
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
    assert [message["type"] for message in messages[7:]] == ["rpc_event"] * 4
    assert [message["event"]["type"] for message in messages[7:]] == [
        "session_turn_context_status",
        "run_started",
        "assistant_text_delta",
        "run_succeeded",
    ]
    assert messages[7]["event"]["status"] == "reused"
    assert messages[7]["event"]["reason"] == "matched"
    assert messages[-1]["event"]["output_text"] == "I created note.txt"

    session_path = session_path_for_id(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=fixed_session_id,
    )
    loaded = load_session(path=session_path, workspace_root=workspace_root)
    assert [run.prompt for run in loaded.runs] == ["create note", "what did you do?"]


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
    assert [message["type"] for message in messages] == ["rpc_event"] * 4
    assert [message["event"]["type"] for message in messages] == [
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
    assert messages[0]["response"]["providers"][1]["provider"] == "openai"
    assert (
        messages[0]["response"]["providers"][1]["default_model_id"]
        == "openai-responses:gpt-5.4"
    )
    assert messages[0]["response"]["providers"][2]["provider"] == "openrouter"
    assert (
        messages[0]["response"]["providers"][2]["default_model_id"]
        == "openrouter:anthropic/claude-sonnet-4-5"
    )
    assert messages[0]["response"]["providers"][4]["provider"] == "google"
    assert (
        messages[0]["response"]["providers"][4]["default_model_id"]
        == "google:gemini-2.5-flash"
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
