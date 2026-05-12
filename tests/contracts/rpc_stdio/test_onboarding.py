import asyncio
import json
import sqlite3
from types import SimpleNamespace

from pydantic_ai.messages import ModelMessage, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from just_another_coding_agent.contracts.mcp import JACA_ONBOARDING_MCP_TOOL_NAMES
from just_another_coding_agent.onboarding import (
    GeneratedMcqQuestion,
    PublishedMcqQuestion,
    SnippetSelection,
    onboarding_db_path,
    publish_onboarding_mcq,
)
from just_another_coding_agent.provider_readiness import ProviderReadinessError
from just_another_coding_agent.rpc.session_store import (
    session_path_for_id,
    workspace_sessions_dir,
)
from just_another_coding_agent.rpc.stdio import (
    _extract_session_id_for_serialization,
    handle_rpc_json_line,
)
from just_another_coding_agent.session import load_session
from tests.contracts.rpc_stdio_test_support import (
    _all_parts,
    create_session_id,
    rpc_messages,
    text_only_stream,
)

ASK_MCQ_MCP_TOOL_NAME = JACA_ONBOARDING_MCP_TOOL_NAMES[0]
GENERATE_MCQ_MCP_TOOL_NAME = JACA_ONBOARDING_MCP_TOOL_NAMES[1]
PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME = JACA_ONBOARDING_MCP_TOOL_NAMES[2]


class _FakeDspyForOnboarding:
    class Signature:
        pass

    @staticmethod
    def InputField(**_kwargs):
        return None

    @staticmethod
    def OutputField(**_kwargs):
        return None

    class Predict:
        def __init__(self, _signature):
            self._prediction = SimpleNamespace(
                prompt="What does pick_model return?",
                option_a="gpt-5.3-codex",
                option_b="gpt-5.4",
                option_c="claude",
                option_d="None",
                correct_index=1,
                explanation="The function returns the gpt-5.4 literal.",
            )

        def set_lm(self, _lm) -> None:
            return None

        def __call__(self, **_kwargs):
            return self._prediction


class _FakeDspyForPacketMcq:
    class Signature:
        pass

    @staticmethod
    def InputField(**_kwargs):
        return None

    @staticmethod
    def OutputField(**_kwargs):
        return None

    class Predict:
        def __init__(self, _signature):
            self._prediction = SimpleNamespace(
                question=(
                    "How does onboarding mode change later tool visibility in "
                    "the same session?"
                ),
                option_a="The Go TUI hardcodes a different tool list locally.",
                option_b=(
                    "The session persists current_mode, and the backend uses "
                    "that effective run mode to resolve the tool names."
                ),
                option_c="The model provider remembers onboarding tools server-side.",
                option_d=(
                    "Only the first /onboard run can ever expose onboarding tools."
                ),
                correct_index=1,
                explanation=(
                    "The backend persists session mode and later resolves tool "
                    "names from that effective run mode."
                ),
            )

        def set_lm(self, _lm) -> None:
            return None

        def __call__(self, **_kwargs):
            return self._prediction


async def test_onboarding_start_persists_and_reopens_pending_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text(
        "def pick_model():\n    return 'gpt-5.4'\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.generate_onboarding_mcq",
        lambda **_kwargs: GeneratedMcqQuestion(
            question_type="mcq",
            snippet=SnippetSelection(
                path="main.py",
                start_line=1,
                end_line=2,
                text="def pick_model():\n    return 'gpt-5.4'",
            ),
            prompt="What does pick_model return?",
            options=("gpt-5.3-codex", "gpt-5.4", "claude", "None"),
            correct_index=1,
            explanation="The function returns the gpt-5.4 literal.",
        ),
    )

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

    first = await rpc_messages(
        request_payload={
            "id": "req-onboard-1",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert first[0]["type"] == "rpc_response"
    response = first[0]["response"]
    assert response["created_session"] is True
    assert response["question_type"] == "mcq"
    assert response["options"] == ["gpt-5.3-codex", "gpt-5.4", "claude", "None"]
    session_id = response["session_id"]
    attempt_id = response["attempt_id"]

    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, prompt FROM onboarding_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
    assert row == ("pending", "What does pick_model return?")

    second = await rpc_messages(
        request_payload={
            "id": "req-onboard-2",
            "command": "onboarding.start",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert second[0]["type"] == "rpc_response"
    reopened = second[0]["response"]
    assert reopened["created_session"] is False
    assert reopened["attempt_id"] == attempt_id


async def test_onboarding_submit_completes_attempt_without_regeneration(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text(
        "def pick_model():\n    return 'gpt-5.4'\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.generate_onboarding_mcq",
        lambda **_kwargs: GeneratedMcqQuestion(
            question_type="mcq",
            snippet=SnippetSelection(
                path="main.py",
                start_line=1,
                end_line=2,
                text="def pick_model():\n    return 'gpt-5.4'",
            ),
            prompt="What does pick_model return?",
            options=("gpt-5.3-codex", "gpt-5.4", "claude", "None"),
            correct_index=1,
            explanation="The function returns the gpt-5.4 literal.",
        ),
    )

    await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    started = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    payload = started[0]["response"]

    submitted = await rpc_messages(
        request_payload={
            "id": "req-onboard-submit",
            "command": "onboarding.submit",
            "payload": {
                "session_id": payload["session_id"],
                "attempt_id": payload["attempt_id"],
                "selected_index": 1,
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    assert submitted == [
        {
            "type": "rpc_response",
            "id": "req-onboard-submit",
            "response": {
                "session_id": payload["session_id"],
                "attempt_id": payload["attempt_id"],
                "question_type": "mcq",
                "selected_index": 1,
                "correct_index": 1,
                "correct_option": "gpt-5.4",
                "is_correct": True,
                "explanation": "The function returns the gpt-5.4 literal.",
            },
        }
    ]

    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, answer_payload_json, result_payload_json
            FROM onboarding_attempts
            WHERE id = ?
            """,
            (payload["attempt_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "completed"
    assert json.loads(row[1]) == {"selected_index": 1}
    assert json.loads(row[2]) == {"correct_index": 1, "is_correct": True}


async def test_onboarding_start_returns_rpc_error_for_generation_runtime_failure(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.generate_onboarding_mcq",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("No supported code snippet was found for onboarding")
        ),
    )

    await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-onboard-start",
            "error_type": "InvalidRequest",
            "message": "No supported code snippet was found for onboarding",
        }
    ]


async def test_onboarding_start_generation_failure_without_session_writes_no_state(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.generate_onboarding_mcq",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("missing dspy")),
    )

    await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-onboard-start",
            "error_type": "InvalidRequest",
            "message": "missing dspy",
        }
    ]

    workspace_dir = workspace_sessions_dir(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    assert not workspace_dir.exists()
    assert not onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    ).exists()


async def test_onboarding_start_invalid_dspy_prediction_returns_rpc_error(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding._select_snippet",
        lambda _workspace_root: SnippetSelection(
            path="main.py",
            start_line=1,
            end_line=2,
            text="def pick_model():\n    return 'gpt-5.4'",
        ),
    )
    fake_dspy = _FakeDspyForOnboarding()
    fake_dspy.Predict = type(
        "InvalidPredict",
        (),
        {
            "__init__": lambda self, _signature: None,
            "set_lm": lambda self, _lm: None,
            "__call__": lambda self, **_kwargs: SimpleNamespace(
                prompt="What does pick_model return?",
                option_a="gpt-5.3-codex",
                option_b="gpt-5.4",
                option_c="claude",
                option_d="None",
                correct_index="B",
                explanation="The function returns the gpt-5.4 literal.",
            ),
        },
    )
    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.import_dspy",
        lambda: fake_dspy,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.build_dspy_lm",
        lambda **_kwargs: object(),
    )

    await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-onboard-start",
            "error_type": "InvalidRequest",
            "message": "invalid literal for int() with base 10: 'B'",
        }
    ]


async def test_onboarding_start_provider_readiness_failure_returns_provider_not_ready(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding._select_snippet",
        lambda _workspace_root: SnippetSelection(
            path="main.py",
            start_line=1,
            end_line=2,
            text="def pick_model():\n    return 'gpt-5.4'",
        ),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.import_dspy",
        lambda: _FakeDspyForOnboarding(),
    )
    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.build_dspy_lm",
        lambda **_kwargs: (_ for _ in ()).throw(
            ProviderReadinessError("openai is not ready: missing_secret")
        ),
    )

    await rpc_messages(
        request_payload={
            "id": "req-trust-accept",
            "command": "workspace.trust_accept",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-onboard-start",
            "error_type": "ProviderNotReady",
            "message": "openai is not ready: missing_secret",
        }
    ]


async def test_onboarding_start_rejects_tool_authored_pending_attempt(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "docs").mkdir()
    (workspace_root / "docs" / "goal.md").write_text(
        "Python owns semantics.\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"

    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    publish_onboarding_mcq(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        session_id=session_id,
        run_id="run-onboard-tool",
        question=PublishedMcqQuestion(
            question_type="mcq",
            packet_ids=("packet-1",),
            prompt="Which doc states Python owns semantics?",
            options=("goal.md", "README.md", "architecture.md", "contracts.md"),
            correct_index=0,
            explanation="docs/goal.md states it directly.",
        ),
    )

    messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-start",
            "command": "onboarding.start",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert messages == [
        {
            "type": "rpc_error",
            "id": "req-onboard-start",
            "error_type": "InvalidRequest",
            "message": (
                "Session has a pending live onboarding tool question that cannot "
                "be reopened through onboarding.start"
            ),
        }
    ]


async def test_run_interrupt_abandons_pending_live_onboarding_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "internal" / "jaca" / "app").mkdir(parents=True)
    (workspace_root / "internal" / "jaca" / "app" / "slash.go").write_text(
        'package app\nvar slashCommands = []string{"/onboard"}\n',
        encoding="utf-8",
    )
    (workspace_root / "main.py").write_text(
        "def pick_model():\n    return 'gpt-5.4'\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    request_payload = {
        "id": "req-run",
        "command": "run.start",
        "payload": {
            "session_id": session_id,
            "prompt": "ask one onboarding question",
            "mode": "onboarding",
        },
    }
    run_messages: list[dict[str, object]] = []
    question_requested = asyncio.Event()

    async def collect_run_messages() -> None:
        async for line in handle_rpc_json_line(
            line=json.dumps(request_payload),
            model=FunctionModel(stream_function=_onboarding_tool_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=lambda _request_id, _event: asyncio.sleep(0),
        ):
            message = json.loads(line)
            run_messages.append(message)
            if (
                message["type"] == "rpc_event"
                and message["event"]["type"] == "onboarding_question_requested"
            ):
                question_requested.set()

    run_task = asyncio.create_task(collect_run_messages())
    await question_requested.wait()

    question_event = next(
        message["event"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "onboarding_question_requested"
    )
    attempt_id = question_event["attempt_id"]

    interrupted = await rpc_messages(
        request_payload={
            "id": "req-interrupt",
            "command": "run.interrupt",
            "payload": {
                "session_id": session_id,
                "promote_queued_steer": False,
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await run_task

    assert interrupted == [
        {
            "type": "rpc_response",
            "id": "req-interrupt",
            "response": {
                "session_id": session_id,
                "promoted_count": 0,
            },
        }
    ]

    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM onboarding_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
    assert row == ("abandoned",)

    monkeypatch.setattr(
        "just_another_coding_agent.onboarding.generate_onboarding_mcq",
        lambda **_kwargs: GeneratedMcqQuestion(
            question_type="mcq",
            snippet=SnippetSelection(
                path="main.py",
                start_line=1,
                end_line=2,
                text="def pick_model():\n    return 'gpt-5.4'",
            ),
            prompt="What does pick_model return?",
            options=("gpt-5.3-codex", "gpt-5.4", "claude", "None"),
            correct_index=1,
            explanation="The function returns the gpt-5.4 literal.",
        ),
    )

    restarted = await rpc_messages(
        request_payload={
            "id": "req-onboard-restart",
            "command": "onboarding.start",
            "payload": {"session_id": session_id},
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    assert restarted[0]["type"] == "rpc_response"
    assert restarted[0]["response"]["attempt_id"] != attempt_id


def _last_user_prompt(messages: list[ModelMessage]) -> str | None:
    prompt: str | None = None
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                prompt = part.content
    return prompt


def _has_onboarding_tool_return(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == ASK_MCQ_MCP_TOOL_NAME
        for part in _all_parts(messages)
    )


def _first_teaching_packet_id(messages: list[ModelMessage]) -> str | None:
    for part in _all_parts(messages):
        if (
            isinstance(part, ToolReturnPart)
            and part.tool_name == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
            and isinstance(part.content, dict)
        ):
            packet_id = part.content.get("packet_id")
            if isinstance(packet_id, str) and packet_id.strip():
                return packet_id
    return None


async def _onboarding_tool_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
    latest_prompt = _last_user_prompt(messages)
    packet_id = _first_teaching_packet_id(messages)
    saw_tool_return = _has_onboarding_tool_return(messages)
    if latest_prompt == "ask one onboarding question" and packet_id is None:
        yield {
            0: DeltaToolCall(
                name=PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "title": "Slash command registry",
                        "concept": (
                            "Slash command handling lives in the TUI layer, "
                            "but onboarding behavior is delegated into the "
                            "backend tool flow."
                        ),
                        "relationships": [
                            {
                                "statement": (
                                    "The slash command entrypoint is declared "
                                    "in slash.go."
                                )
                            },
                            {
                                "statement": (
                                    "The same file exposes the /onboard "
                                    "trigger that leads into backend-owned "
                                    "onboarding behavior."
                                )
                            },
                        ],
                        "snippets": [
                            {
                                "path": "internal/jaca/app/slash.go",
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "path": "internal/jaca/app/slash.go",
                                "start_line": 2,
                                "end_line": 2,
                            },
                        ],
                    }
                ),
                tool_call_id="tool-packet-1",
            )
        }
        return
    if latest_prompt == "ask one onboarding question" and not saw_tool_return:
        yield {
            0: DeltaToolCall(
                name=ASK_MCQ_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "packet_ids": [packet_id],
                        "question": "Which file defines the slash command table?",
                        "options": [
                            "internal/jaca/app/model.go",
                            "internal/jaca/app/slash.go",
                            "internal/jaca/app/render.go",
                            "internal/jaca/rpc/client.go",
                        ],
                        "correct_index": 1,
                        "explanation": (
                            "The slash command registry is declared in "
                            "internal/jaca/app/slash.go."
                        ),
                    }
                ),
                tool_call_id="tool-onboarding-1",
            )
        }
        return
    if saw_tool_return:
        yield "done"
        return
    raise AssertionError(f"unexpected prompt/tool state: {latest_prompt!r}")


async def _onboarding_tool_without_packet_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
    latest_prompt = _last_user_prompt(messages)
    saw_tool_return = _has_onboarding_tool_return(messages)
    if (
        latest_prompt == "ask one onboarding question without packet"
        and not saw_tool_return
    ):
        yield {
            0: DeltaToolCall(
                name=ASK_MCQ_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "packet_ids": ["packet-missing"],
                        "question": "Which file defines the slash command table?",
                        "options": [
                            "internal/jaca/app/model.go",
                            "internal/jaca/app/slash.go",
                            "internal/jaca/app/render.go",
                            "internal/jaca/rpc/client.go",
                        ],
                        "correct_index": 1,
                        "explanation": (
                            "The slash command registry is declared in "
                            "internal/jaca/app/slash.go."
                        ),
                    }
                ),
                tool_call_id="tool-onboarding-1",
            )
        }
        return
    if saw_tool_return:
        yield "done"
        return
    raise AssertionError(f"unexpected prompt/tool state: {latest_prompt!r}")


async def _teaching_packet_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
    latest_prompt = _last_user_prompt(messages)
    saw_packet_return = any(
        isinstance(part, ToolReturnPart)
        and part.tool_name == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
        for part in _all_parts(messages)
    )
    if latest_prompt == "teach me dispatch" and not saw_packet_return:
        yield {
            0: DeltaToolCall(
                name=PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "title": "Slash command dispatch",
                        "concept": (
                            "Onboarding mode is a backend-owned run mode that "
                            "changes tool visibility for later turns."
                        ),
                        "relationships": [
                            {
                                "statement": (
                                    "The slash command triggers onboarding "
                                    "mode, and the backend persists that mode "
                                    "into session metadata."
                                )
                            },
                            {
                                "statement": (
                                    "The persisted run mode then determines "
                                    "which tool names the backend exposes on "
                                    "the next run."
                                )
                            },
                        ],
                        "snippets": [
                            {
                                "path": "internal/jaca/app/slash.go",
                                "start_line": 1,
                                "end_line": 2,
                            },
                            {
                                "path": "internal/jaca/app/model.go",
                                "start_line": 1,
                                "end_line": 2,
                            },
                        ],
                    }
                ),
                tool_call_id="tool-packet-1",
            )
        }
        return
    if saw_packet_return:
        yield "done"
        return
    raise AssertionError(f"unexpected prompt/tool state: {latest_prompt!r}")


async def _doc_teaching_packet_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
    latest_prompt = _last_user_prompt(messages)
    saw_packet_return = any(
        isinstance(part, ToolReturnPart)
        and part.tool_name == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
        for part in _all_parts(messages)
    )
    if latest_prompt == "teach me with docs" and not saw_packet_return:
        yield {
            0: DeltaToolCall(
                name=PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "title": "Docs only packet",
                        "concept": (
                            "Documentation should be rejected for teaching packets."
                        ),
                        "relationships": [
                            {
                                "statement": (
                                    "This packet incorrectly points at docs "
                                    "instead of code."
                                )
                            }
                        ],
                        "snippets": [
                            {
                                "path": "docs/goal.md",
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "path": "docs/goal.md",
                                "start_line": 2,
                                "end_line": 3,
                            },
                        ],
                    }
                ),
                tool_call_id="tool-packet-docs-1",
            )
        }
        return
    if saw_packet_return:
        yield "done"
        return
    raise AssertionError(f"unexpected prompt/tool state: {latest_prompt!r}")


def _first_generated_mcq(messages: list[ModelMessage]) -> dict[str, object] | None:
    for part in _all_parts(messages):
        if (
            isinstance(part, ToolReturnPart)
            and part.tool_name == GENERATE_MCQ_MCP_TOOL_NAME
            and isinstance(part.content, dict)
        ):
            return part.content
    return None


async def test_run_start_supports_live_onboarding_question_tool_without_dspy(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "internal" / "jaca" / "app").mkdir(parents=True)
    (workspace_root / "internal" / "jaca" / "app" / "slash.go").write_text(
        'package app\nvar slashCommands = []string{"/onboard"}\n',
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    request_payload = {
        "id": "req-run",
        "command": "run.start",
        "payload": {
            "session_id": session_id,
            "prompt": "ask one onboarding question",
            "mode": "onboarding",
        },
    }
    run_messages: list[dict[str, object]] = []
    question_requested = asyncio.Event()

    async def collect_run_messages() -> None:
        async for line in handle_rpc_json_line(
            line=json.dumps(request_payload),
            model=FunctionModel(stream_function=_onboarding_tool_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=lambda _request_id, _event: asyncio.sleep(0),
        ):
            message = json.loads(line)
            run_messages.append(message)
            if (
                message["type"] == "rpc_event"
                and message["event"]["type"] == "onboarding_question_requested"
            ):
                question_requested.set()

    run_task = asyncio.create_task(collect_run_messages())
    await question_requested.wait()

    question_event = next(
        message["event"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "onboarding_question_requested"
    )
    attempt_id = question_event["attempt_id"]
    assert question_event["question_type"] == "mcq"
    assert question_event["prompt"] == "Which file defines the slash command table?"
    assert question_event["options"] == [
        "internal/jaca/app/model.go",
        "internal/jaca/app/slash.go",
        "internal/jaca/app/render.go",
        "internal/jaca/rpc/client.go",
    ]

    submit_messages = await rpc_messages(
        request_payload={
            "id": "req-onboard-submit",
            "command": "onboarding.submit",
            "payload": {
                "session_id": session_id,
                "attempt_id": attempt_id,
                "selected_index": 1,
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await run_task

    assert submit_messages == [
        {
            "type": "rpc_response",
            "id": "req-onboard-submit",
            "response": {
                "session_id": session_id,
                "attempt_id": attempt_id,
                "question_type": "mcq",
                "selected_index": 1,
                "correct_index": 1,
                "correct_option": "internal/jaca/app/slash.go",
                "is_correct": True,
                "explanation": (
                    "The slash command registry is declared in "
                    "internal/jaca/app/slash.go."
                ),
            },
        }
    ]
    assert run_messages[-1]["type"] == "rpc_response"
    event_types = [
        message["event"]["type"]
        for message in run_messages
        if message["type"] == "rpc_event"
    ]
    assert "onboarding_question_requested" in event_types
    assert "tool_call_started" in event_types
    assert "tool_call_succeeded" in event_types
    assert event_types[-1] == "run_succeeded"

    session = load_session(
        path=session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=session_id,
        ),
        workspace_root=workspace_root,
    )
    packet_return = next(
        part
        for message in session.message_history
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
    )

    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                status,
                prompt,
                question_payload_json,
                answer_payload_json,
                result_payload_json
            FROM onboarding_attempts
            WHERE id = ?
            """,
            (attempt_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "completed"
    assert row[1] == "Which file defines the slash command table?"
    assert json.loads(row[2]) == {
        "packet_ids": [packet_return.content["packet_id"]],
        "options": [
            "internal/jaca/app/model.go",
            "internal/jaca/app/slash.go",
            "internal/jaca/app/render.go",
            "internal/jaca/rpc/client.go",
        ],
        "correct_index": 1,
    }
    assert json.loads(row[3]) == {"selected_index": 1}
    assert json.loads(row[4]) == {"correct_index": 1, "is_correct": True}
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == ASK_MCQ_MCP_TOOL_NAME
        for message in session.message_history
        for part in message.parts
    )


async def test_run_start_rejects_mcq_without_linked_teaching_packet(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "internal" / "jaca" / "app").mkdir(parents=True)
    (workspace_root / "internal" / "jaca" / "app" / "slash.go").write_text(
        'var slashCommands = []string{"/onboard"}\n',
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    run_messages = await rpc_messages(
        request_payload={
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "ask one onboarding question without packet",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=_onboarding_tool_without_packet_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    mcq_result = next(
        message["event"]["result"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "tool_call_succeeded"
        and message["event"]["tool_name"] == ASK_MCQ_MCP_TOOL_NAME
    )
    assert mcq_result == {
        "ok": False,
        "error_type": "ToolOperationalError",
        "message": (
            "ask_mcq_question requires packet_ids that refer to teaching "
            "packets published earlier in this same run"
        ),
    }


async def test_run_start_supports_teaching_packet_tool_in_onboarding_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "internal" / "jaca" / "app").mkdir(parents=True)
    (workspace_root / "internal" / "jaca" / "app" / "slash.go").write_text(
        'package app\nvar slashCommands = []string{"/onboard"}\n',
        encoding="utf-8",
    )
    (workspace_root / "internal" / "jaca" / "app" / "model.go").write_text(
        "package app\nfunc submitPrompt() {}\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    run_messages = await rpc_messages(
        request_payload={
            "id": "req-run-packet",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "teach me dispatch",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=_teaching_packet_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    succeeded_event = next(
        message["event"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "tool_call_succeeded"
        and message["event"]["tool_name"] == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
    )
    assert succeeded_event["activity"]["title"] == "Slash command dispatch"
    assert succeeded_event["activity"]["display_label"] == "Teach"
    assert succeeded_event["activity"]["summary"] == "showing 2 snippets"
    assert succeeded_event["activity"]["details"] == {
        "kind": "mcp",
        "server_id": "jaca_onboarding",
        "tool_name": "publish_teaching_packet",
        "model_tool_name": PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME,
        "provenance": {
            "source": "top_level_model",
            "parent_tool_call_id": None,
            "code_mode_cell_id": None,
        },
        "failure": None,
        "wrapped_title": "Slash command dispatch",
        "wrapped_display_label": "Teach",
        "wrapped_summary": "showing 2 snippets",
        "wrapped_details": {
            "kind": "teaching_packet",
            "concept": (
                "Onboarding mode is a backend-owned run mode that changes tool "
                "visibility for later turns."
            ),
            "relationships": [
                {
                    "statement": (
                        "The slash command triggers onboarding mode, and the "
                        "backend persists that mode into session metadata."
                    )
                },
                {
                    "statement": (
                        "The persisted run mode then determines which tool names "
                        "the backend exposes on the next run."
                    )
                },
            ],
            "snippets": [
                {
                    "path": "internal/jaca/app/slash.go",
                    "start_line": 1,
                    "end_line": 2,
                    "text": 'package app\nvar slashCommands = []string{"/onboard"}',
                },
                {
                    "path": "internal/jaca/app/model.go",
                    "start_line": 1,
                    "end_line": 2,
                    "text": "package app\nfunc submitPrompt() {}",
                },
            ],
        },
    }

    session = load_session(
        path=session_path_for_id(
            sessions_root=sessions_root,
            workspace_root=workspace_root,
            session_id=session_id,
        ),
        workspace_root=workspace_root,
    )
    packet_return = next(
        part
        for message in session.message_history
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
    )
    assert isinstance(packet_return.content["packet_id"], str)
    assert packet_return.content["packet_id"] != ""
    assert packet_return.content == {
        "packet_id": packet_return.content["packet_id"],
        "title": "Slash command dispatch",
        "concept": (
            "Onboarding mode is a backend-owned run mode that changes tool "
            "visibility for later turns."
        ),
        "relationships": [
            {
                "statement": (
                    "The slash command triggers onboarding mode, and the "
                    "backend persists that mode into session metadata."
                )
            },
            {
                "statement": (
                    "The persisted run mode then determines which tool names "
                    "the backend exposes on the next run."
                )
            },
        ],
        "snippet_count": 2,
        "snippets": [
            {
                "path": "internal/jaca/app/slash.go",
                "start_line": 1,
                "end_line": 2,
                "text": 'package app\nvar slashCommands = []string{"/onboard"}',
            },
            {
                "path": "internal/jaca/app/model.go",
                "start_line": 1,
                "end_line": 2,
                "text": "package app\nfunc submitPrompt() {}",
            },
        ],
    }


async def _generate_mcq_from_packet_stream(
    messages: list[ModelMessage],
    _agent_info: object,
):
    latest_prompt = _last_user_prompt(messages)
    packet_id = _first_teaching_packet_id(messages)
    generated_mcq = _first_generated_mcq(messages)
    saw_question_tool_return = _has_onboarding_tool_return(messages)
    if latest_prompt == "teach me and then quiz me" and packet_id is None:
        yield {
            0: DeltaToolCall(
                name=PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "title": "Slash command dispatch",
                        "concept": (
                            "The /onboard slash command switches the session "
                            "into onboarding mode, and that durable mode "
                            "changes later tool visibility."
                        ),
                        "relationships": [
                            {
                                "statement": (
                                    "/onboard requests onboarding mode from the "
                                    "TUI entrypoint."
                                )
                            },
                            {
                                "statement": (
                                    "The persisted session mode becomes the "
                                    "effective run mode on later turns."
                                )
                            },
                            {
                                "statement": (
                                    "The effective run mode drives the toolset "
                                    "the backend exposes."
                                )
                            },
                        ],
                        "snippets": [
                            {
                                "path": "internal/jaca/app/onboarding.go",
                                "start_line": 1,
                                "end_line": 2,
                            },
                            {
                                "path": (
                                    "src/just_another_coding_agent/contracts/session.py"
                                ),
                                "start_line": 1,
                                "end_line": 2,
                            },
                            {
                                "path": "src/just_another_coding_agent/rpc/stdio.py",
                                "start_line": 1,
                                "end_line": 2,
                            },
                            {
                                "path": (
                                    "src/just_another_coding_agent/tools/registry.py"
                                ),
                                "start_line": 1,
                                "end_line": 2,
                            },
                        ],
                    }
                ),
                tool_call_id="tool-packet-1",
            )
        }
        return
    if (
        latest_prompt == "teach me and then quiz me"
        and packet_id is not None
        and generated_mcq is None
    ):
        yield {
            0: DeltaToolCall(
                name=GENERATE_MCQ_MCP_TOOL_NAME,
                json_args=json.dumps(
                    {
                        "packet_ids": [packet_id],
                    }
                ),
                tool_call_id="tool-mcq-draft-1",
            )
        }
        return
    if (
        latest_prompt == "teach me and then quiz me"
        and generated_mcq is not None
        and not saw_question_tool_return
    ):
        yield {
            0: DeltaToolCall(
                name=ASK_MCQ_MCP_TOOL_NAME,
                json_args=json.dumps(generated_mcq),
                tool_call_id="tool-onboarding-1",
            )
        }
        return
    if saw_question_tool_return:
        yield "done"
        return
    raise AssertionError(
        "unexpected prompt/tool state: "
        f"{latest_prompt!r}, packet_id={packet_id!r}, generated={generated_mcq!r}"
    )


async def test_run_start_can_generate_mcq_from_teaching_packet(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "just_another_coding_agent.tools.mcq_from_teaching_packets.import_dspy",
        lambda: _FakeDspyForPacketMcq,
    )
    monkeypatch.setattr(
        "just_another_coding_agent.tools.mcq_from_teaching_packets.build_dspy_lm",
        lambda **_kwargs: object(),
    )
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "internal" / "jaca" / "app").mkdir(parents=True)
    (workspace_root / "internal" / "jaca" / "app" / "onboarding.go").write_text(
        "package app\nfunc executeOnboardSlash() {}\n",
        encoding="utf-8",
    )
    (workspace_root / "src" / "just_another_coding_agent" / "contracts").mkdir(
        parents=True
    )
    (
        workspace_root
        / "src"
        / "just_another_coding_agent"
        / "contracts"
        / "session.py"
    ).write_text(
        "class SessionMetadata:\n    current_mode = DEFAULT_RUN_MODE\n",
        encoding="utf-8",
    )
    (workspace_root / "src" / "just_another_coding_agent" / "rpc").mkdir(parents=True)
    (
        workspace_root / "src" / "just_another_coding_agent" / "rpc" / "stdio.py"
    ).write_text(
        "effective_run_mode = request.payload.mode if "
        "request.payload.mode is not None else "
        "session_metadata.current_mode\n"
        "tool_names = resolve_tool_names_for_run_mode(effective_run_mode)\n",
        encoding="utf-8",
    )
    (workspace_root / "src" / "just_another_coding_agent" / "tools").mkdir(parents=True)
    (
        workspace_root / "src" / "just_another_coding_agent" / "tools" / "registry.py"
    ).write_text(
        "def resolve_tool_names_for_run_mode(mode):\n"
        "    if mode == ONBOARDING_RUN_MODE:\n"
        "        return tuple(KNOWN_TOOL_NAMES)\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    request_payload = {
        "id": "req-run-draft",
        "command": "run.start",
        "payload": {
            "session_id": session_id,
            "prompt": "teach me and then quiz me",
            "mode": "onboarding",
        },
    }
    run_messages: list[dict[str, object]] = []
    question_requested = asyncio.Event()

    async def collect_run_messages() -> None:
        async for line in handle_rpc_json_line(
            line=json.dumps(request_payload),
            model=FunctionModel(stream_function=_generate_mcq_from_packet_stream),
            workspace_root=workspace_root,
            sessions_root=sessions_root,
            emit_rpc_event=lambda _request_id, _event: asyncio.sleep(0),
        ):
            message = json.loads(line)
            run_messages.append(message)
            if (
                message["type"] == "rpc_event"
                and message["event"]["type"] == "onboarding_question_requested"
            ):
                question_requested.set()

    run_task = asyncio.create_task(collect_run_messages())
    await question_requested.wait()

    question_event = next(
        message["event"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "onboarding_question_requested"
    )
    assert question_event["question_type"] == "mcq"
    assert len(question_event["options"]) == 4
    attempt_id = question_event["attempt_id"]

    submit_messages = await rpc_messages(
        request_payload={
            "id": "req-draft-submit",
            "command": "onboarding.submit",
            "payload": {
                "session_id": session_id,
                "attempt_id": attempt_id,
                "selected_index": 0,
            },
        },
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )
    await run_task

    assert submit_messages[0]["type"] == "rpc_response"
    generated_result = next(
        message["event"]["result"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "tool_call_succeeded"
        and message["event"]["tool_name"] == GENERATE_MCQ_MCP_TOOL_NAME
    )
    assert generated_result["packet_ids"]
    assert generated_result["question"]
    assert len(generated_result["options"]) == 4
    assert 0 <= generated_result["correct_index"] <= 3
    assert generated_result["explanation"]


async def test_run_start_rejects_teaching_packet_with_docs_snippet(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "docs").mkdir()
    (workspace_root / "docs" / "goal.md").write_text(
        "# Goal\n\nDocs are not code snippets.\n",
        encoding="utf-8",
    )
    sessions_root = tmp_path / "sessions"
    session_id = await create_session_id(
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    run_messages = await rpc_messages(
        request_payload={
            "id": "req-run-doc-packet",
            "command": "run.start",
            "payload": {
                "session_id": session_id,
                "prompt": "teach me with docs",
                "mode": "onboarding",
            },
        },
        model=FunctionModel(stream_function=_doc_teaching_packet_stream),
        workspace_root=workspace_root,
        sessions_root=sessions_root,
    )

    packet_result = next(
        message["event"]["result"]
        for message in run_messages
        if message["type"] == "rpc_event"
        and message["event"]["type"] == "tool_call_succeeded"
        and message["event"]["tool_name"] == PUBLISH_TEACHING_PACKET_MCP_TOOL_NAME
    )
    assert packet_result == {
        "ok": False,
        "error_type": "ToolOperationalError",
        "message": (
            "publish_teaching_packet accepts code files only; "
            "documentation paths are not allowed"
        ),
    }


def test_onboarding_submit_is_not_session_serialized() -> None:
    line = json.dumps(
        {
            "id": "req-onboard-submit",
            "command": "onboarding.submit",
            "payload": {
                "session_id": "0123456789abcdef0123456789abcdef",
                "attempt_id": "attempt-1",
                "selected_index": 1,
            },
        }
    )

    assert _extract_session_id_for_serialization(line) is None
