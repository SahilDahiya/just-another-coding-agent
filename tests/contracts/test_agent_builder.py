from datetime import date

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.runtime import (
    CANONICAL_AGENT_INSTRUCTIONS,
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
    )

    assert instructions.startswith(CANONICAL_AGENT_INSTRUCTIONS)
    assert (
        "Prefer read to examine files instead of bash cat or sed." in instructions
    )
    assert (
        "Use only these tools: read, write, edit, bash, grep, ls, find."
        in instructions
    )
    assert "Use grep for content search across files." in instructions
    assert "Use ls for bounded directory listings." in instructions
    assert "Use find for file discovery by glob pattern." in instructions
    assert "Use bash for builds and commands." in instructions
    assert (
        "Do not claim you created, edited, or saved a file unless you "
        "actually used write or edit, or verified the result with read or bash."
        in instructions
    )
    assert (
        "After code changes or required file outputs, run the smallest "
        "relevant verification step before concluding."
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
    )

    assert (
        "Do not claim you created, edited, or saved a file unless you "
        "actually used write or edit, or verified the result with read or bash."
        in instructions
    )
    assert (
        "After code changes or required file outputs, run the smallest "
        "relevant verification step before concluding."
        in instructions
    )


def test_build_canonical_model_settings_include_thinking_when_set() -> None:
    assert build_canonical_model_settings(thinking="high") == {"thinking": "high"}
    assert build_canonical_model_settings(thinking=True) == {"thinking": True}
    assert build_canonical_model_settings() is None
