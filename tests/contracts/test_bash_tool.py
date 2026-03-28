import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic_ai import CallDeferred

from just_another_coding_agent.tools.bash import bash, execute_bash
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolCommandError, ToolEncodingError


@dataclass(frozen=True)
class _FakeRunContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = None
    tool_name: str | None = None


async def test_bash_tool_runs_in_explicit_workspace_root(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = await execute_bash(
        workspace_root=workspace_root,
        command="pwd",
    )

    assert result["exit_code"] == 0
    assert result["output"].strip() == str(workspace_root)


async def test_bash_tool_fails_on_non_zero_exit_and_includes_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolCommandError, match="boom\n\nCommand exited with code 7"):
        await execute_bash(
            workspace_root=workspace_root,
            command="printf 'boom' >&2; exit 7",
        )


async def test_bash_tool_returns_empty_output_when_command_prints_nothing(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = await execute_bash(
        workspace_root=workspace_root,
        command=":",
    )

    assert result == {"exit_code": 0, "output": ""}


async def test_execute_bash_accepts_minimal_execution_context_and_streams_updates(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    updates: list[tuple[str, str, object | None]] = []

    async def sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        updates.append((tool_call_id, tool_name, payload))

    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            tool_update_sink=sink,
        ),
        tool_call_id="call-bash",
        tool_name="bash",
    )

    result = await execute_bash(
        ctx=ctx,
        workspace_root=workspace_root,
        command="printf 'hello'",
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert updates == [
        ("call-bash", "bash", {"output": "hello"}),
    ]

async def test_bash_tool_can_request_deferred_execution(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(CallDeferred):
        await bash(
            _FakeRunContext(
                deps=WorkspaceDeps.from_workspace_root(workspace_root),
                tool_call_id="call-bash",
                tool_name="bash",
            ),
            "pytest -q",
            defer=True,
        )


async def test_bash_tool_fails_on_timeout(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        ToolCommandError,
        match="partial output\n\nCommand timed out after 1 seconds",
    ):
        await execute_bash(
            workspace_root=workspace_root,
            command="printf 'partial output'; sleep 2",
            timeout=1,
        )


async def test_bash_tool_truncates_large_output_and_saves_full_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = await execute_bash(
        workspace_root=workspace_root,
        command=(
            "python - <<'PY'\n"
            "for i in range(1, 2105):\n"
            '    print(f"line {i}")\n'
            "PY"
        ),
    )

    assert result["exit_code"] == 0
    output = result["output"]
    assert isinstance(output, str)
    assert "line 1\n" not in output
    assert "line 104\n" not in output
    assert "line 105\n" in output
    assert "line 2104\n" in output
    assert "[Showing lines " in output
    assert "Full output: " in output
    match = re.search(r"Full output: (?P<path>[^\]]+)\]", output)
    assert match is not None
    full_output_path = match.group("path")
    assert "line 1\n" in Path(full_output_path).read_text(encoding="utf-8")


async def test_bash_tool_fails_for_invalid_utf8_output(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolEncodingError):
        await execute_bash(
            workspace_root=workspace_root,
            command="python -c \"import sys; sys.stdout.buffer.write(b'\\xff')\"",
        )
