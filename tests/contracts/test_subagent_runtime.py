from datetime import date

import pytest
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.contracts.run_events import (
    RunStartedEvent,
    RunSucceededEvent,
)
from just_another_coding_agent.runtime.project_docs import (
    PROJECT_DOC_MESSAGE_HEADER,
)
from just_another_coding_agent.runtime.prompt_layers import build_base_product_prompt
from just_another_coding_agent.runtime.subagent import (
    EPHEMERAL_SUBAGENT_TOOL_NAMES,
    EphemeralSubagentSpec,
    build_ephemeral_subagent_agent,
    build_ephemeral_subagent_instructions,
    build_ephemeral_subagent_tool_names,
    build_ephemeral_subagent_workspace_deps,
    get_subagent_role_spec,
    stream_ephemeral_subagent_run_events,
)
from just_another_coding_agent.runtime.turn_context import (
    RUNTIME_CONTEXT_MESSAGE_HEADER,
)
from just_another_coding_agent.tools.deps import RunSessionScope, WorkspaceDeps


async def text_only_stream(_messages, _agent_info):
    yield "done"


def _message_texts(messages) -> list[str]:
    return [message.parts[0].content for message in messages]


def test_build_base_product_prompt_can_restrict_tool_policy_to_inspection_tools(
) -> None:
    prompt = build_base_product_prompt(tool_names=EPHEMERAL_SUBAGENT_TOOL_NAMES)

    assert "Use only these tools: read, grep, find, ls." in prompt
    assert "Use grep for content search across files." in prompt
    assert "Use ls for bounded directory listings." in prompt
    assert "Use find for file discovery by glob pattern." in prompt
    assert (
        "This run is inspection-only. Do not claim you created, edited, or "
        "saved files."
        in prompt
    )
    assert "Use edit for precise surgical changes" not in prompt
    assert "Use write only for new files or complete rewrites." not in prompt
    assert "Use shell for builds, commands, and verification." not in prompt
    assert (
        "After code changes or required file outputs, run the smallest "
        "relevant verification step before concluding."
        not in prompt
    )


def test_shell_capable_subagent_tool_policy_is_not_described_as_read_only() -> None:
    prompt = build_base_product_prompt(
        tool_names=build_ephemeral_subagent_tool_names("shell")
    )

    assert "Use only these tools: read, grep, find, ls, shell." in prompt
    assert "Use shell for builds, commands, and verification." in prompt
    assert "This run is read-only." not in prompt
    assert "You do not have write or edit tools in this run." in prompt


def test_subagent_role_specs_are_backend_owned() -> None:
    explore = get_subagent_role_spec("explore")
    verify = get_subagent_role_spec("verification")

    assert explore.display_label == "Explore"
    assert explore.running_summary == "exploring repository"
    assert any("locating the relevant files" in line for line in explore.prompt_lines)

    assert verify.display_label == "Verify"
    assert verify.running_summary == "verifying repository state"
    assert any(
        "checking claims against the repository state" in line
        for line in verify.prompt_lines
    )


async def test_build_ephemeral_subagent_agent_uses_default_capability_instructions(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_ephemeral_subagent_agent(
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        role="explore",
        spawn_mode="fresh",
        capability="default",
    )

    with capture_run_messages() as messages:
        async for _event in agent.run_stream_events(
            "scan",
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
        ):
            pass

    first_request = messages[0]
    assert isinstance(first_request, ModelRequest)
    assert first_request.instructions == build_ephemeral_subagent_instructions(
        role="explore",
        spawn_mode="fresh",
        capability="default",
    )
    assert "Use only these tools: read, grep, find, ls." in first_request.instructions
    assert (
        "You are an ephemeral child agent handling one bounded task."
        in first_request.instructions
    )
    assert (
        "You start without inheriting the parent conversation history."
        in first_request.instructions
    )
    assert (
        "Follow any output-shape instructions in the assigned task exactly."
        in first_request.instructions
    )
    assert (
        "If the task does not specify an output shape, return concise plain "
        "text findings."
        in first_request.instructions
    )
    assert (
        "Do not add markdown fences unless the task asks for them."
        in first_request.instructions
    )


async def test_shell_capable_subagent_agent_uses_shell_capable_instructions(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_ephemeral_subagent_agent(
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        role="verification",
        spawn_mode="fresh",
        capability="shell",
    )

    with capture_run_messages() as messages:
        async for _event in agent.run_stream_events(
            "scan",
            deps=WorkspaceDeps.from_workspace_root(workspace_root),
        ):
            pass

    first_request = messages[0]
    assert isinstance(first_request, ModelRequest)
    assert (
        "Use only these tools: read, grep, find, ls, shell."
        in first_request.instructions
    )
    assert (
        "Use shell for builds, commands, and verification."
        in first_request.instructions
    )
    assert (
        "When the task needs local commands, scripts, or parsing beyond "
        "read/grep/find/ls, use shell directly and keep the work bounded."
        in first_request.instructions
    )
    assert (
        "You do not have write or edit tools in this run."
        in first_request.instructions
    )


def test_build_ephemeral_subagent_workspace_deps_inherits_parent_runtime_frame(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    parent_deps = WorkspaceDeps.from_workspace_root(workspace_root)
    spec = EphemeralSubagentSpec(
        name="compaction-scan",
        role="explore",
        spawn_mode="fresh",
        capability="default",
        task="Find where compaction resets turn context.",
        parent_session_id="a" * 32,
        parent_run_id="run-1",
        parent_tool_call_id="call-1",
    )

    child_deps = build_ephemeral_subagent_workspace_deps(
        parent_deps=parent_deps,
        spec=spec,
    )

    assert parent_deps.session_scope == RunSessionScope()
    assert child_deps.workspace_root == parent_deps.workspace_root
    assert child_deps.shell_family == parent_deps.shell_family
    assert child_deps.read_only_worker is not parent_deps.read_only_worker
    assert child_deps.session_scope == RunSessionScope(
        kind="subagent",
        name="compaction-scan",
        parent_session_id="a" * 32,
        parent_run_id="run-1",
        parent_tool_call_id="call-1",
    )


async def test_stream_ephemeral_subagent_run_events_builds_fresh_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").write_text(
        "Read docs/README.md first.\n",
        encoding="utf-8",
    )
    spec = EphemeralSubagentSpec(
        name="compaction-scan",
        role="explore",
        spawn_mode="fresh",
        capability="default",
        task="Find where compaction resets turn context.",
        parent_session_id="a" * 32,
        parent_run_id="run-1",
        parent_tool_call_id="call-1",
    )
    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
        **_kwargs,
    ):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["message_history"] = message_history
        captured["instructions"] = instructions
        captured["thinking"] = thinking
        captured["deps"] = deps
        captured["message_history_sink"] = message_history_sink
        yield RunStartedEvent(run_id="sub-run-1")
        yield RunSucceededEvent(run_id="sub-run-1", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.subagent.stream_run_events",
        fake_stream_run_events,
    )

    events = [
        event
        async for event in stream_ephemeral_subagent_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            spec=spec,
            current_date=date(2026, 4, 10),
            shell_family="posix",
            thinking="medium",
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    assert captured["prompt"] == "Find where compaction resets turn context."
    assert captured["instructions"] is None
    assert captured["thinking"] == "medium"
    assert captured["deps"] == build_ephemeral_subagent_workspace_deps(
        parent_deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family="posix",
        ),
        spec=spec,
    )
    message_texts = _message_texts(captured["message_history"])
    assert any(
        text.startswith(PROJECT_DOC_MESSAGE_HEADER)
        for text in message_texts
    )
    assert any(
        text.startswith(RUNTIME_CONTEXT_MESSAGE_HEADER)
        for text in message_texts
    )


async def test_stream_ephemeral_subagent_run_events_forks_sanitized_parent_history(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    spec = EphemeralSubagentSpec(
        name="compaction-scan",
        role="verification",
        spawn_mode="fork",
        capability="default",
        task="Check the parent claim.",
        parent_session_id="a" * 32,
        parent_run_id="run-1",
        parent_tool_call_id="call-1",
    )
    parent_message_history = (
        ModelRequest(
            parts=[
                SystemPromptPart(content="system"),
                UserPromptPart(content="parent prompt"),
            ],
            instructions="system",
        ),
        ModelResponse(parts=[TextPart(content="prior answer")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="subagent",
                    args={"name": "compaction-scan"},
                    tool_call_id="call-1",
                )
            ]
        ),
    )
    captured: dict[str, object] = {}

    async def fake_stream_run_events(
        *,
        agent,
        prompt,
        message_history=None,
        instructions=None,
        thinking=None,
        deps=None,
        message_history_sink=None,
        **_kwargs,
    ):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["message_history"] = message_history
        captured["instructions"] = instructions
        captured["thinking"] = thinking
        captured["deps"] = deps
        captured["message_history_sink"] = message_history_sink
        yield RunStartedEvent(run_id="sub-run-1")
        yield RunSucceededEvent(run_id="sub-run-1", output_text="done")

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.subagent.stream_run_events",
        fake_stream_run_events,
    )

    events = [
        event
        async for event in stream_ephemeral_subagent_run_events(
            model=FunctionModel(stream_function=text_only_stream),
            workspace_root=workspace_root,
            spec=spec,
            parent_message_history=parent_message_history,
        )
    ]

    assert [event.type for event in events] == ["run_started", "run_succeeded"]
    inherited = captured["message_history"]
    assert inherited is not None
    assert len(inherited) == 2
    assert isinstance(inherited[0], ModelRequest)
    assert inherited[0].instructions is None
    assert [type(part).__name__ for part in inherited[0].parts] == ["UserPromptPart"]
    assert inherited[0].parts[0].content == "parent prompt"
    assert isinstance(inherited[1], ModelResponse)
    assert inherited[1].parts[0].content == "prior answer"


async def test_stream_ephemeral_subagent_run_events_rejects_fork_without_parent_history(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    spec = EphemeralSubagentSpec(
        name="compaction-scan",
        role="verification",
        spawn_mode="fork",
        capability="default",
        task="Check the parent claim.",
        parent_session_id="a" * 32,
        parent_run_id="run-1",
        parent_tool_call_id="call-1",
    )

    with pytest.raises(
        RuntimeError,
        match="Forked subagent runs require parent message history",
    ):
        _ = [
            event
            async for event in stream_ephemeral_subagent_run_events(
                model=FunctionModel(stream_function=text_only_stream),
                workspace_root=workspace_root,
                spec=spec,
            )
        ]
