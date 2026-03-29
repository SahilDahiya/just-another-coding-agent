import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai import CallDeferred

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.tools import ShellToolInput
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolCommandError, ToolEncodingError
from just_another_coding_agent.tools.shell import execute_shell, shell


@dataclass(frozen=True)
class _FakeRunContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = None
    tool_name: str | None = None


def _test_shell_family() -> str:
    return detect_default_shell_family()


def _powershell_available() -> bool:
    executable = (
        "powershell.exe"
        if detect_default_shell_family() == "powershell"
        else "pwsh"
    )
    return shutil.which(executable) is not None


def _workspace_command() -> str:
    if _test_shell_family() == "powershell":
        return "Get-Location | Select-Object -ExpandProperty Path"
    return "pwd"


def _non_zero_command() -> str:
    if _test_shell_family() == "powershell":
        return "Write-Error 'boom'; exit 7"
    return "printf boom; exit 7"


def _empty_output_command() -> str:
    if _test_shell_family() == "powershell":
        return "$null = 1"
    return ":"


def _hello_command() -> str:
    if _test_shell_family() == "powershell":
        return "[Console]::Out.Write('hello')"
    return "printf hello"


def _timeout_command() -> str:
    if _test_shell_family() == "powershell":
        return "[Console]::Out.Write('partial output'); Start-Sleep -Seconds 2"
    return "printf 'partial output'; sleep 2"


def _large_output_command() -> str:
    if _test_shell_family() == "powershell":
        return '1..2104 | ForEach-Object { "line $_" }'
    return "i=1; while [ $i -le 2104 ]; do printf 'line %s\\n' \"$i\"; i=$((i+1)); done"


def _invalid_utf8_command() -> str:
    if _test_shell_family() == "powershell":
        return "[Console]::OpenStandardOutput().WriteByte(255)"
    return "printf '\\377'"


async def test_shell_tool_runs_in_explicit_workspace_root(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = await execute_shell(
        tool_input=ShellToolInput(command=_workspace_command()),
        workspace_root=workspace_root,
        shell_family=_test_shell_family(),
    )

    assert result["exit_code"] == 0
    assert result["output"].strip() == str(workspace_root)


async def test_shell_tool_fails_on_non_zero_exit_and_includes_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        ToolCommandError,
        match=r"boom[\s\S]*Command exited with code 7",
    ):
        await execute_shell(
            tool_input=ShellToolInput(command=_non_zero_command()),
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
        )


async def test_shell_tool_returns_empty_output_when_command_prints_nothing(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = await execute_shell(
        tool_input=ShellToolInput(command=_empty_output_command()),
        workspace_root=workspace_root,
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": ""}


async def test_execute_shell_accepts_minimal_execution_context_and_streams_updates(
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
            shell_family=_test_shell_family(),
            tool_update_sink=sink,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        tool_input=ShellToolInput(command=_hello_command()),
        workspace_root=workspace_root,
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert updates == [
        ("call-shell", "shell", {"output": "hello"}),
    ]


def test_shell_tool_rejects_empty_command() -> None:
    with pytest.raises(ValidationError):
        ShellToolInput(command="")


def test_shell_tool_rejects_non_string_command() -> None:
    with pytest.raises(ValidationError):
        ShellToolInput(command=123)


def test_shell_tool_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        ShellToolInput(command="pwd", timeout=0)


def test_shell_tool_accepts_explicit_defer_flag() -> None:
    assert ShellToolInput(command="pytest", defer=True).defer is True


async def test_shell_tool_can_request_deferred_execution(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(CallDeferred):
        await shell(
            _FakeRunContext(
                deps=WorkspaceDeps.from_workspace_root(workspace_root),
                tool_call_id="call-shell",
                tool_name="shell",
            ),
            "pytest -q",
            defer=True,
        )


async def test_shell_tool_fails_on_timeout(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        ToolCommandError,
        match="partial output\n\nCommand timed out after 1 seconds",
    ):
        await execute_shell(
            tool_input=ShellToolInput(
                command=_timeout_command(),
                timeout=1,
            ),
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
        )


async def test_shell_tool_truncates_large_output_and_saves_full_output(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = await execute_shell(
        tool_input=ShellToolInput(command=_large_output_command()),
        workspace_root=workspace_root,
        shell_family=_test_shell_family(),
    )

    assert result["exit_code"] == 0
    output = result["output"]
    assert isinstance(output, str)
    assert "line 1\n" not in output
    assert "line 104\n" not in output
    normalized_output = output.replace("\r\n", "\n")
    assert "line 105\n" in normalized_output
    assert "line 2104\n" in normalized_output
    assert "[Showing lines " in output
    assert "Full output: " in output
    match = re.search(r"Full output: (?P<path>[^\]]+)\]", output)
    assert match is not None
    full_output_path = match.group("path")
    assert "line 1\n" in Path(full_output_path).read_text(encoding="utf-8")


async def test_shell_tool_fails_for_invalid_utf8_output(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolEncodingError):
        await execute_shell(
            tool_input=ShellToolInput(command=_invalid_utf8_command()),
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
        )


@pytest.mark.skipif(
    not _powershell_available(),
    reason="PowerShell runner not available on this host",
)
async def test_execute_shell_supports_explicit_powershell_runner(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    result = await execute_shell(
        tool_input=ShellToolInput(command="[Console]::Out.Write('ok')"),
        workspace_root=workspace_root,
        shell_family="powershell",
    )

    assert result == {"exit_code": 0, "output": "ok"}


async def test_execute_shell_uses_posix_runner_for_posix_shell_family(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    observed: dict[str, object] = {}

    class _FakeStdout:
        async def read(self, _count: int) -> bytes:
            if observed.get("read_once"):
                return b""
            observed["read_once"] = True
            return b"ok"

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 123
            self.returncode = 0
            self.stdout = _FakeStdout()

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        "just_another_coding_agent.tools.shell.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await execute_shell(
        tool_input=ShellToolInput(command="pwd"),
        workspace_root=workspace_root,
        shell_family="posix",
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert observed["args"][:2] == ("sh", "-lc")
