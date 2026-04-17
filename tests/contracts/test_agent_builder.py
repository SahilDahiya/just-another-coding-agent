from datetime import date

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel

from just_another_coding_agent.contracts.sandbox import EffectiveCapabilities
from just_another_coding_agent.runtime import (
    CANONICAL_AGENT_INSTRUCTIONS,
    CANONICAL_AGENT_OUTPUT_RETRIES,
    build_canonical_agent,
    build_canonical_instructions,
    build_canonical_model_settings,
    build_runtime_context_text,
    build_static_agent_instructions,
)
from just_another_coding_agent.runtime.prompt_layers import (
    BASE_PRODUCT_PROMPT_SECTIONS,
    build_base_product_prompt,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps


async def text_only_stream(
    _messages: list[ModelMessage],
    _agent_info: object,
):
    yield "done"


async def test_build_canonical_agent_sets_default_instructions(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agent = build_canonical_agent(
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        tool_names=[],
    )

    with capture_run_messages() as messages:
        async for _event in agent.run_stream_events(
            "hi",
            deps=WorkspaceDeps(workspace_root),
        ):
            pass

    first_request = messages[0]
    assert isinstance(first_request, ModelRequest)
    assert first_request.instructions == build_static_agent_instructions(
        tool_names=[]
    )
    assert isinstance(first_request.parts[0], UserPromptPart)
    assert first_request.parts[0].content == "hi"


def test_build_canonical_instructions_include_dynamic_context(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    instructions = build_canonical_instructions(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
        shell_family="powershell",
    )

    assert instructions.startswith(CANONICAL_AGENT_INSTRUCTIONS)
    assert instructions.endswith(
        build_runtime_context_text(
            workspace_root=workspace_root,
            current_date=date(2026, 3, 26),
            shell_family="powershell",
        )
    )
    assert (
        "Prefer read to examine files instead of shelling out just to view files."
        in instructions
    )
    assert (
        "Use only these tools: read, write, edit, shell, grep, ls, find, subagent."
        in instructions
    )
    assert "Use grep for content search across files." in instructions
    assert "Use ls for bounded directory listings." in instructions
    assert "Use find for file discovery by glob pattern." in instructions
    assert (
        "Use subagent for one bounded side task when either a fresh or "
        "forked child pass would help."
        in instructions
    )
    assert (
        "Good fits: locating relevant files or evidence, checking one "
        "claim against repository state, or inspecting one large "
        "artifact for the parent."
        in instructions
    )
    assert (
        "By default the child gets read, grep, find, and ls only; "
        "request shell capability only when the child needs local "
        "commands or scripts."
        in instructions
    )
    assert (
        "Prefer spawn_mode='fork' so the child can build on the parent's "
        "current conversation or tool context; use spawn_mode='fresh' only "
        "for an independent repo or artifact pass."
        in instructions
    )
    assert (
        "Do not use subagent for broad multi-step work or when the next "
        "local command is already obvious."
        in instructions
    )
    assert (
        "When you spawn a child, make the task detailed enough to "
        "succeed: state the exact goal, relevant files or artifacts, "
        "constraints, stop condition, and desired report shape when "
        "needed."
        in instructions
    )
    assert "Use shell for builds, commands, and verification." in instructions
    assert "Current shell family: powershell" in instructions
    assert (
        "Do not claim you created, edited, or saved a file unless you "
        "actually used write or edit, or verified the result with read or shell."
        in instructions
    )
    assert (
        "After code changes or required file outputs, run the smallest "
        "relevant verification step before concluding." in instructions
    )
    assert (
        "When the user asks to run tests, lint, or another obvious "
        "verification step, run the narrowest relevant command directly; "
        "inspect first only if the command or scope is ambiguous."
        in instructions
    )
    assert "Current date: 2026-03-26" in instructions
    assert f"Current workspace root: {workspace_root.resolve()}" in instructions


def test_build_canonical_instructions_include_truthfulness_and_verification_rules(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    instructions = build_canonical_instructions(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
        shell_family="posix",
    )

    assert (
        "Do not claim you created, edited, or saved a file unless you "
        "actually used write or edit, or verified the result with read or shell."
        in instructions
    )
    assert (
        "After code changes or required file outputs, run the smallest "
        "relevant verification step before concluding." in instructions
    )


def test_static_agent_instructions_include_response_style_contract() -> None:
    instructions = build_static_agent_instructions()

    assert "Default response style: brief, direct, and outcome-first." in instructions
    assert (
        "Do not restate the user's request or narrate routine process "
        "unless that context is necessary."
        in instructions
    )
    assert (
        "During work, keep progress updates to one short sentence focused "
        "on the next action or concrete finding."
        in instructions
    )
    assert (
        "Final answers should usually be one short paragraph: state what "
        "changed or what you found, then mention verification or blockers."
        in instructions
    )
    assert (
        "Use bullets only when there are multiple distinct findings, "
        "steps, or options."
        in instructions
    )
    assert (
        "If no files changed, answer the question directly without a "
        "change-style summary."
        in instructions
    )


def test_base_product_prompt_sections_have_stable_order() -> None:
    assert [section.name for section in BASE_PRODUCT_PROMPT_SECTIONS] == [
        "identity",
        "tool_policy",
        "tool_failure_policy",
        "verification_policy",
        "failure_semantics",
        "response_style",
        "filesystem_truth",
    ]
    assert CANONICAL_AGENT_INSTRUCTIONS == build_base_product_prompt()
    assert build_static_agent_instructions() == build_base_product_prompt()


def test_build_runtime_context_text_is_dynamic_only(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    runtime_context_text = build_runtime_context_text(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
        shell_family="powershell",
    )

    assert runtime_context_text == "\n".join(
        [
            "Current date: 2026-03-26",
            f"Current workspace root: {workspace_root.resolve()}",
            "Current shell family: powershell",
        ]
    )
    assert CANONICAL_AGENT_INSTRUCTIONS not in runtime_context_text


def test_build_runtime_context_text_includes_visible_model_framing_when_given(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    runtime_context_text = build_runtime_context_text(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
        shell_family="powershell",
        timezone="America/Los_Angeles",
        model_label="openai-responses:gpt-5.3-codex",
        thinking="high",
    )

    assert runtime_context_text == "\n".join(
        [
            "Current date: 2026-03-26",
            "Current timezone: America/Los_Angeles",
            f"Current workspace root: {workspace_root.resolve()}",
            "Current shell family: powershell",
            "Current model: openai-responses:gpt-5.3-codex",
            "Current thinking setting: high",
        ]
    )


def test_build_runtime_context_text_includes_effective_capabilities_when_given(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    runtime_context_text = build_runtime_context_text(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
        shell_family="powershell",
        effective_capabilities=EffectiveCapabilities(
            filesystem_access="workspace_write",
            network_access="restricted",
            execution_isolation="sandboxed",
            approval_mode="on_escalation",
        ),
    )

    assert runtime_context_text == "\n".join(
        [
            "Current date: 2026-03-26",
            f"Current workspace root: {workspace_root.resolve()}",
            "Current shell family: powershell",
            "Current filesystem access: workspace_write",
            "Current network access: restricted",
            "Current execution isolation: sandboxed",
            "Current approval policy: on_escalation",
        ]
    )


def test_build_canonical_model_settings_include_thinking_when_set() -> None:
    assert build_canonical_model_settings(thinking="high") == {"thinking": "high"}
    assert build_canonical_model_settings(thinking=True) == {"thinking": True}
    assert build_canonical_model_settings() is None


def test_build_canonical_agent_resolves_string_models(tmp_path, monkeypatch) -> None:
    from just_another_coding_agent.runtime.models import unwrap_instrumented_model

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    agent = build_canonical_agent(
        model="openai-responses:gpt-5.3-codex",
        workspace_root=workspace_root,
        tool_names=[],
    )
    # Unwrap instrumentation to check the underlying model type
    unwrapped_model = unwrap_instrumented_model(agent.model)
    assert isinstance(unwrapped_model, OpenAIResponsesModel)
    assert agent.model.model_name == "gpt-5.3-codex"


def test_build_canonical_agent_documents_plain_text_output_retry_policy(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    agent = build_canonical_agent(
        model=FunctionModel(stream_function=text_only_stream),
        workspace_root=workspace_root,
        tool_names=[],
    )

    assert agent.output_type is str
    assert agent._max_result_retries == CANONICAL_AGENT_OUTPUT_RETRIES
    # Malformed tool correction is runtime-owned, not framework-owned.
    assert agent._max_tool_retries == 0
