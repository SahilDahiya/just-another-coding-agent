from datetime import date
from pathlib import Path

import pytest
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from just_another_coding_agent.runtime import (
    CANONICAL_AGENT_INSTRUCTIONS,
    build_canonical_agent,
    build_canonical_instructions,
)


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
        async for _event in agent.run_stream_events("hi"):
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
        "Use bash for search, inspection, builds, and commands (ls, rg, find, grep)."
        in instructions
    )
    assert "Current date: 2026-03-26" in instructions
    assert f"Current workspace root: {workspace_root.resolve()}" in instructions


def test_build_canonical_instructions_include_workspace_agents_md(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agents_path = workspace_root / "AGENTS.md"
    agents_path.write_text("repo rule 1\nrepo rule 2\n", encoding="utf-8")

    instructions = build_canonical_instructions(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
    )

    assert "# Project Context" in instructions
    assert "repo rule 1\nrepo rule 2" in instructions


def test_build_canonical_instructions_omit_project_context_without_agents_md(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    instructions = build_canonical_instructions(
        workspace_root=workspace_root,
        current_date=date(2026, 3, 26),
    )

    assert "# Project Context" not in instructions


def test_build_canonical_instructions_fail_for_invalid_utf8_agents_md(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agents_path = workspace_root / "AGENTS.md"
    agents_path.write_bytes(b"\xff\xfe")

    with pytest.raises(
        RuntimeError,
        match=f"Workspace AGENTS.md is not valid UTF-8: {agents_path}",
    ):
        build_canonical_instructions(
            workspace_root=workspace_root,
            current_date=date(2026, 3, 26),
        )


def test_build_canonical_instructions_fail_for_unreadable_agents_md(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agents_path = workspace_root / "AGENTS.md"
    agents_path.write_text("repo rule\n", encoding="utf-8")
    original_read_text = Path.read_text

    def failing_read_text(self: Path, *args, **kwargs) -> str:
        if self == agents_path:
            raise PermissionError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    with pytest.raises(
        RuntimeError,
        match=f"Workspace AGENTS.md could not be read: {agents_path}",
    ):
        build_canonical_instructions(
            workspace_root=workspace_root,
            current_date=date(2026, 3, 26),
        )
