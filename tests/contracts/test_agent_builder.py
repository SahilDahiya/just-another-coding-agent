from datetime import date

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel

from just_another_coding_agent.runtime import (
    CANONICAL_AGENT_INSTRUCTIONS,
    CANONICAL_AGENT_OUTPUT_RETRIES,
    CANONICAL_AGENT_TOOL_CORRECTION_RETRIES,
    build_canonical_agent,
    build_canonical_instructions,
    build_canonical_model_settings,
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
    assert first_request.instructions == build_canonical_instructions(
        workspace_root=workspace_root,
        current_date=date.today(),
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
    assert (
        "Prefer read to examine files instead of shelling out just to view files."
        in instructions
    )
    assert (
        "Use only these tools: read, write, edit, shell, grep, ls, find, "
        "work_list, work_read, work_create, work_update, work_status."
        in instructions
    )
    assert "Use grep for content search across files." in instructions
    assert "Use ls for bounded directory listings." in instructions
    assert "Use find for file discovery by glob pattern." in instructions
    assert "Use shell for builds, commands, and verification." in instructions
    assert (
        "Use work_list and work_read when durable workspace work tracking is "
        "relevant."
        in instructions
    )
    assert (
        "Before using work_create, check for an obvious existing match with "
        "work_list or work_read."
        in instructions
    )
    assert (
        "Use work_status to change durable work-item status." in instructions
    )
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


def test_build_canonical_agent_uses_model_aware_live_compaction_limit(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    observed: dict[str, int] = {}

    def fake_build_in_run_history_processor(*, soft_char_limit: int):
        observed["soft_char_limit"] = soft_char_limit
        return lambda messages: messages

    monkeypatch.setattr(
        "just_another_coding_agent.runtime.agent.build_in_run_history_processor",
        fake_build_in_run_history_processor,
    )

    build_canonical_agent(
        model="openai-responses:gpt-5.3-codex",
        workspace_root=workspace_root,
        tool_names=[],
    )

    assert observed["soft_char_limit"] == 1_280_000


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
    assert agent._max_tool_retries == CANONICAL_AGENT_TOOL_CORRECTION_RETRIES
