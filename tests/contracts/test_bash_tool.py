import asyncio
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from just_another_coding_agent.contracts.platform import detect_default_shell_family
from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    NormalizedSandboxPolicy,
    WorkspaceWriteSandboxPolicy,
    WorkspaceWriteStrictSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools._activity import truncate_activity_label
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolCommandError, ToolEncodingError
from just_another_coding_agent.tools.shell import (
    DEFAULT_SHELL_TIMEOUT_SECONDS,
    SHELL_PUBLISH_MIN_INTERVAL_SECONDS,
    execute_shell,
)


@dataclass(frozen=True)
class _FakeRunContext:
    deps: WorkspaceDeps
    tool_call_id: str | None = None
    tool_name: str | None = None


class _ExecutorHandle:
    def __init__(
        self,
        *,
        chunks: list[bytes] | None = None,
        exit_code: int = 0,
    ) -> None:
        self._chunks = [b"ok"] if chunks is None else list(chunks)
        self._exit_code = exit_code

    async def read(self, _max_bytes: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    async def wait(self) -> int:
        return self._exit_code

    async def terminate(self) -> None:
        return None

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


def _test_shell_family() -> str:
    return detect_default_shell_family()


def _powershell_available() -> bool:
    executable = (
        "powershell.exe" if detect_default_shell_family() == "powershell" else "pwsh"
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
        workspace_root=workspace_root,
        command=_workspace_command(),
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
            workspace_root=workspace_root,
            command=_non_zero_command(),
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
        workspace_root=workspace_root,
        command=_empty_output_command(),
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
        workspace_root=workspace_root,
        command=_hello_command(),
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert updates == [
        ("call-shell", "shell", {"output": "hello"}),
    ]


async def test_execute_shell_requests_approval_when_policy_is_always(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceDeps.from_workspace_root(
                    workspace_root
                ).permission_state.sandbox_policy,
                approval_policy=ApprovalPolicy(mode="always"),
                effective_capabilities=WorkspaceDeps.from_workspace_root(
                    workspace_root
                ).permission_state.effective_capabilities.model_copy(
                    update={"approval_mode": "always"}
                ),
            ),
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=_hello_command(),
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "hello"}
    assert len(requests) == 1
    assert requests[0].reason.startswith("allow shell command:")
    assert requests[0].requested_capabilities.approval_mode == "always"
    assert requests[0].requested_permissions is None


async def test_execute_shell_requests_approval_for_network_escalation(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executed_requests = []

    class _Executor:
        async def execute(self, request):
            executed_requests.append(request)
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="curl https://example.com",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert len(requests) == 1
    assert requests[0].requested_capabilities.network_access == "enabled"
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.network_access == "enabled"
    assert len(executed_requests) == 1
    assert executed_requests[0].selected_sandbox_mode == "workspace_write"
    assert executed_requests[0].normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="enabled"),
        execution_isolation="sandboxed",
    )


async def test_execute_shell_skips_approval_for_explicit_outside_read_root(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executed_requests = []

    class _Executor:
        async def execute(self, request):
            executed_requests.append(request)
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=f"cat {outside_root / 'README.md'}",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert requests == []
    assert len(executed_requests) == 1
    assert executed_requests[0].normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )
    assert ctx.deps.permission_memory.approved_read_roots == set()


async def test_execute_shell_requests_outside_read_approval_in_strict_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executed_requests = []

    class _Executor:
        async def execute(self, request):
            executed_requests.append(request)
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteStrictSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=f"cat {outside_root / 'README.md'}",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert len(requests) == 1
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_read_roots == (
        str(outside_root),
    )
    assert len(executed_requests) == 1
    assert executed_requests[0].selected_sandbox_mode == "workspace_write_strict"
    assert executed_requests[0].normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(
            access="workspace_write",
            extra_read_roots=(str(outside_root),),
        ),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )
    assert ctx.deps.permission_memory.approved_read_roots == {str(outside_root)}


async def test_execute_shell_keeps_outside_read_approval_free_with_session_memory(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executed_requests = []

    class _Executor:
        async def execute(self, request):
            executed_requests.append(request)
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    deps = WorkspaceDeps(
        workspace_root=workspace_root,
        shell_family=_test_shell_family(),
        sandbox_executor=_Executor(),
        approval_requester=approval_requester,
        permission_state=permission_state,
    )
    deps.permission_memory.remember_read_root(str(outside_root))
    ctx = _FakeRunContext(
        deps=deps,
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=f'bash -lc "cat {outside_root / "README.md"}"',
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert requests == []
    assert len(executed_requests) == 1
    assert executed_requests[0].normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )


async def test_execute_shell_skips_approval_for_local_command(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []
    executed_requests = []

    class _Executor:
        async def execute(self, request):
            executed_requests.append(request)
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command="printf ok",
        shell_family=_test_shell_family(),
    )

    assert result == {"exit_code": 0, "output": "ok"}
    assert requests == []
    assert len(executed_requests) == 1
    assert executed_requests[0].selected_sandbox_mode == "workspace_write"
    assert executed_requests[0].normalized_policy == NormalizedSandboxPolicy(
        filesystem=FileSystemSandboxPolicy(access="workspace_write"),
        network=NetworkSandboxPolicy(access="restricted"),
        execution_isolation="sandboxed",
    )


async def test_execute_shell_fails_fast_when_approval_is_required_without_requester(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    host_permission_state = WorkspaceDeps.from_workspace_root(
        workspace_root
    ).permission_state
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            permission_state=build_permission_state(
                sandbox_policy=host_permission_state.sandbox_policy,
                approval_policy=ApprovalPolicy(mode="always"),
                effective_capabilities=host_permission_state.effective_capabilities.model_copy(
                    update={"approval_mode": "always"}
                ),
            ),
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    with pytest.raises(
        RuntimeError,
        match="Shell execution requires approval",
    ):
        await execute_shell(
            ctx=ctx,
            workspace_root=workspace_root,
            command=_hello_command(),
            shell_family=_test_shell_family(),
        )


async def test_execute_shell_discloses_network_and_mount_scope_in_approval_reason(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "other"
    outside_root.mkdir()
    monkeypatch.chdir(tmp_path)
    requests = []

    class _Executor:
        async def execute(self, request):
            return _ExecutorHandle()

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            approval_requester=approval_requester,
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=(
            f'bash -lc "curl https://example.com && '
            f'cat {outside_root / "README.md"}"'
        ),
        shell_family=_test_shell_family(),
    )

    assert len(requests) == 1
    expected_command = truncate_activity_label(
        f'bash -lc "curl https://example.com && cat {outside_root / "README.md"}"'
    )
    assert requests[0].reason == (
        "allow shell command: "
        f"{expected_command} "
        "(network enabled)"
    )


async def test_execute_shell_adds_network_guidance_when_restricted_command_fails(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    class _Executor:
        async def execute(self, request):
            assert request.normalized_policy.network.access == "restricted"
            return _ExecutorHandle(
                chunks=[b"Temporary failure in name resolution"],
                exit_code=1,
            )

    permission_state = build_permission_state(
        sandbox_policy=WorkspaceWriteSandboxPolicy(),
        approval_policy=ApprovalPolicy(mode="on_escalation"),
    )
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            sandbox_executor=_Executor(),
            permission_state=permission_state,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    with pytest.raises(
        ToolCommandError,
        match=(
            "Temporary failure in name resolution[\\s\\S]*"
            "approve network access"
        ),
    ):
        await execute_shell(
            ctx=ctx,
            workspace_root=workspace_root,
            command=(
                'python -c "import socket; '
                "socket.getaddrinfo('example.com', 80)\""
            ),
            shell_family=_test_shell_family(),
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
            workspace_root=workspace_root,
            command=_timeout_command(),
            shell_family=_test_shell_family(),
            timeout=1,
        )


async def test_shell_tool_uses_backend_default_timeout_when_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "just_another_coding_agent.tools.shell.DEFAULT_SHELL_TIMEOUT_SECONDS",
        1,
    )

    with pytest.raises(
        ToolCommandError,
        match="partial output\n\nCommand timed out after 1 seconds",
    ):
        await execute_shell(
            workspace_root=workspace_root,
            command=_timeout_command(),
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
        workspace_root=workspace_root,
        command=_large_output_command(),
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
            workspace_root=workspace_root,
            command=_invalid_utf8_command(),
            shell_family=_test_shell_family(),
        )


async def test_shell_tool_activity_reports_effective_default_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.chdir(tmp_path)
    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    from just_another_coding_agent.tools.shell import shell

    result = await shell(ctx, _hello_command())

    assert result.metadata["details"]["timeout"] == DEFAULT_SHELL_TIMEOUT_SECONDS


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
        workspace_root=workspace_root,
        command="[Console]::Out.Write('ok')",
        shell_family="powershell",
    )

    assert result == {"exit_code": 0, "output": "ok"}


# Regression tests for the "git show --unified=40 on a multi-file commit
# wedged the TUI on Windows" class of bug. See commits 7c31d24, 38e60ca, and
# the project memory project_jaca_rpc_writer_pipe_backpressure.md for the
# full debugging write-up. These tests target the underlying mechanisms the
# wedge depended on (publisher coalescing and reader/publisher decoupling)
# rather than the specific git command, because (a) the command's output
# size depends on repo state and (b) the bug was never really about git.


def _burst_output_command(lines: int, bytes_per_line: int) -> str:
    """A posix/powershell command that emits `lines` lines as fast as possible.

    Used to exercise the shell tool's reader/publisher pipeline under a
    high-rate child. Each line is plain ASCII of the given size, followed
    by a newline.
    """
    payload = "x" * bytes_per_line
    python_script = (
        f"import sys\n"
        f"for _ in range({lines}):\n"
        f"    sys.stdout.write('{payload}\\n')\n"
        f"sys.stdout.flush()\n"
    )
    # Use the running Python interpreter so the test is deterministic and
    # does not depend on `python3` being on PATH.
    python = (
        sys.executable.replace("\\", "/")
        if sys.platform == "win32"
        else sys.executable
    )
    if detect_default_shell_family() == "powershell":
        # Pass the script via -c (PowerShell executes arg-0 as the program
        # path, so we just invoke python.exe directly with -c).
        escaped = python_script.replace('"', '`"').replace("'", "`'")
        return f'& "{python}" -c "{escaped}"'
    escaped = python_script.replace('"', '\\"')
    return f'{python} -c "{escaped}"'


async def test_execute_shell_coalesces_partial_updates_at_min_interval(
    tmp_path,
) -> None:
    """The publisher loop must throttle partial updates to at most one per
    SHELL_PUBLISH_MIN_INTERVAL_SECONDS regardless of how fast the child
    process writes. This is the O(N²) payload growth fix — without it, a
    fast multi-chunk child would emit one partial update per read chunk,
    saturating the RPC pipe with (cumulatively) megabytes of JSON for a
    50 KB output.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    publish_times: list[float] = []

    async def recording_sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        del tool_call_id, tool_name, payload
        publish_times.append(time.monotonic())

    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            tool_update_sink=recording_sink,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    # 200 lines × 512 bytes = ~100 KB, which far exceeds SHELL_MAX_BYTES
    # and will be truncated. The key property is the output is produced
    # across many 4 KB reader chunks — if the publisher did not coalesce,
    # we'd expect ~25 publish calls. With coalescing at 250 ms and a run
    # that takes well under a second, we expect at most a handful.
    start = time.monotonic()
    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=_burst_output_command(lines=200, bytes_per_line=512),
        shell_family=_test_shell_family(),
        timeout=30,
    )
    elapsed = time.monotonic() - start

    assert result["exit_code"] == 0, result
    assert len(publish_times) >= 1, "publisher should emit at least one update"

    # Verify gap between successive publications is at least the configured
    # minimum interval (with a small tolerance for scheduling jitter).
    min_interval_with_slack = SHELL_PUBLISH_MIN_INTERVAL_SECONDS - 0.05
    gaps = [
        publish_times[i] - publish_times[i - 1]
        for i in range(1, len(publish_times))
    ]
    for gap in gaps:
        assert gap >= min_interval_with_slack, (
            f"publisher fired within {gap:.3f}s of the previous update, "
            f"expected >= {min_interval_with_slack:.3f}s. All gaps: {gaps}"
        )

    # Sanity-check that coalescing actually reduced the publish count
    # below the chunk count. A ~100 KB output read in 4 KB chunks gives
    # ~25 reader wake-ups; with coalescing at 250 ms we expect at most
    # ceil(elapsed / 250 ms) + 1 publications.
    max_expected = int(elapsed / SHELL_PUBLISH_MIN_INTERVAL_SECONDS) + 2
    assert len(publish_times) <= max_expected, (
        f"publisher emitted {len(publish_times)} updates over {elapsed:.2f}s, "
        f"expected at most {max_expected} with coalescing interval "
        f"{SHELL_PUBLISH_MIN_INTERVAL_SECONDS}s"
    )


async def test_execute_shell_completes_when_tool_update_sink_is_slow(
    tmp_path,
) -> None:
    """A slow tool_update_sink must NOT delay execute_shell's completion.
    The publisher task is decoupled from the reader task via asyncio.Event,
    so even if every sink call blocks for a long time, the reader keeps
    draining stdout, the process exits, and execute_shell returns with the
    full result. The in-flight publisher call is cancelled in the finally
    block.

    This is the regression guard for the "slow tool_update_sink wedges the
    reader" bug that was one of the early theories about the wedge.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    sink_call_count = 0

    async def slow_sink(
        tool_call_id: str,
        tool_name: str,
        payload: object | None,
    ) -> None:
        del tool_call_id, tool_name, payload
        nonlocal sink_call_count
        sink_call_count += 1
        # Simulate a TUI queue / event sink that blocks for a long time
        # (in real life: because it is parked trying to render a huge
        # partial update, or because its downstream consumer is slow).
        await asyncio.sleep(5)

    ctx = _FakeRunContext(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            shell_family=_test_shell_family(),
            tool_update_sink=slow_sink,
        ),
        tool_call_id="call-shell",
        tool_name="shell",
    )

    start = time.monotonic()
    result = await execute_shell(
        ctx=ctx,
        workspace_root=workspace_root,
        command=_burst_output_command(lines=50, bytes_per_line=256),
        shell_family=_test_shell_family(),
        timeout=30,
    )
    elapsed = time.monotonic() - start

    # execute_shell must complete in well under the 5-second sink-sleep,
    # proving the reader was not waiting on the publisher. Generous upper
    # bound to tolerate CI scheduler jitter.
    assert result["exit_code"] == 0, result
    assert elapsed < 3.0, (
        f"execute_shell took {elapsed:.2f}s despite a decoupled publisher; "
        f"the reader may be waiting on the publisher/sink (sink_call_count="
        f"{sink_call_count})"
    )
    # The output must be the full, complete result — decoupling the
    # publisher from the reader must not drop bytes.
    assert "xxxxxxxxxx" in str(result["output"])
