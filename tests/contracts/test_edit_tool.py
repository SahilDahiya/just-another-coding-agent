from types import SimpleNamespace

import pytest

from just_another_coding_agent.contracts.sandbox import (
    ApprovalDecision,
    ApprovalPolicy,
    WorkspaceWriteSandboxPolicy,
    build_permission_state,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import EditResult, edit, execute_edit
from just_another_coding_agent.tools.errors import (
    ToolEncodingError,
    ToolMatchError,
    ToolPathError,
)


def test_edit_tool_replaces_exact_unique_text(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    result = execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text="world",
        new_text="agent",
    )

    assert path.read_bytes() == b"hello\nagent\n"
    assert isinstance(result, EditResult)
    assert result.path == str(path)
    assert "-world" in result.diff
    assert "+agent" in result.diff
    assert result.added_lines == 1
    assert result.removed_lines == 1


def test_edit_tool_counts_changed_lines_that_look_like_diff_headers(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("keep\n--- separator\n", encoding="utf-8")

    result = execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text="--- separator\n",
        new_text="+++ separator\n",
    )

    assert isinstance(result, EditResult)
    assert result.added_lines == 1
    assert result.removed_lines == 1
    assert "---- separator" in result.diff
    assert "++++ separator" in result.diff


def test_edit_tool_allows_deleting_text(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text="world\n",
        new_text="",
    )

    assert path.read_bytes() == b"hello\n"


def test_edit_tool_fails_for_missing_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ToolPathError):
        execute_edit(
            workspace_root=workspace_root,
            path="missing.txt",
            old_text="old",
            new_text="new",
        )


def test_edit_tool_fails_for_directory_target(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "nested").mkdir()

    with pytest.raises(ToolPathError):
        execute_edit(
            workspace_root=workspace_root,
            path="nested",
            old_text="old",
            new_text="new",
        )


def test_edit_tool_fails_for_invalid_utf8(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ToolEncodingError):
        execute_edit(
            workspace_root=workspace_root,
            path="binary.bin",
            old_text="old",
            new_text="new",
        )


def test_edit_tool_fails_when_old_text_is_missing(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    with pytest.raises(ToolMatchError, match="old_text must match exactly once"):
        execute_edit(
            workspace_root=workspace_root,
            path="note.txt",
            old_text="missing",
            new_text="agent",
        )


def test_edit_tool_fails_when_old_text_is_ambiguous(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\nworld\n")

    with pytest.raises(ToolMatchError, match="old_text must match exactly once"):
        execute_edit(
            workspace_root=workspace_root,
            path="note.txt",
            old_text="world",
            new_text="agent",
        )


def test_edit_tool_fails_when_replacement_is_no_op(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\nworld\n")

    with pytest.raises(ToolMatchError, match="Edit would not change file contents"):
        execute_edit(
            workspace_root=workspace_root,
            path="note.txt",
            old_text="world",
            new_text="world",
        )


def test_edit_tool_allows_relative_path_that_resolves_outside_workspace(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"hello\nworld\n")

    result = execute_edit(
        workspace_root=workspace_root,
        path="../outside.txt",
        old_text="world",
        new_text="agent",
    )

    assert isinstance(result, EditResult)
    assert result.path == str(outside.resolve())
    assert outside.read_bytes() == b"hello\nagent\n"


def test_edit_tool_matches_old_text_without_bom_in_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes("\ufeffhello\nworld\n".encode("utf-8"))

    result = execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text="hello\nworld\n",
        new_text="hello\nagent\n",
    )

    assert isinstance(result, EditResult)
    assert result.path == str(path)
    assert path.read_bytes() == "\ufeffhello\nagent\n".encode("utf-8")


def test_edit_tool_matches_lf_old_text_against_crlf_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_bytes(b"hello\r\nworld\r\n")

    execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text="hello\nworld\n",
        new_text="hello\nagent\n",
    )

    assert path.read_bytes() == b"hello\r\nagent\r\n"


def test_edit_tool_falls_back_to_normalized_matching(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text("say “hello”\u00a0-\u00a0world  \n", encoding="utf-8")

    execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text='say "hello" - world\n',
        new_text='say "hello" - agent\n',
    )

    assert path.read_text(encoding="utf-8") == 'say "hello" - agent\n'


async def test_edit_requests_approval_for_outside_workspace_path_in_default_mode(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("hello\nworld\n", encoding="utf-8")
    requests = []

    async def approval_requester(request):
        requests.append(request)
        return ApprovalDecision(
            request_id=request.request_id,
            decision="approved",
        )

    ctx = SimpleNamespace(
        deps=WorkspaceDeps(
            workspace_root=workspace_root,
            approval_requester=approval_requester,
            permission_state=build_permission_state(
                sandbox_policy=WorkspaceWriteSandboxPolicy(),
                approval_policy=ApprovalPolicy(mode="on_escalation"),
            ),
        )
    )

    result = await edit(ctx, "../outside.txt", "world", "agent")

    assert result.return_value == f"Edited {outside.resolve()}"
    assert outside.read_text(encoding="utf-8") == "hello\nagent\n"
    assert len(requests) == 1
    assert requests[0].reason == (
        "allow edit outside workspace: ../outside.txt "
        f"(writable roots: {outside.resolve().parent})"
    )
    assert requests[0].requested_permissions is not None
    assert requests[0].requested_permissions.extra_write_roots == (
        str(outside.resolve().parent),
    )


def test_edit_tool_fuzzy_fallback_preserves_unmatched_surrounding_content(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    path = workspace_root / "note.txt"
    path.write_text(
        "keep “smart”\nchange “hello”\u00a0-\u00a0world  \n",
        encoding="utf-8",
    )

    execute_edit(
        workspace_root=workspace_root,
        path="note.txt",
        old_text='change "hello" - world\n',
        new_text='change "hello" - agent\n',
    )

    assert path.read_text(encoding="utf-8") == (
        'keep “smart”\nchange "hello" - agent\n'
    )
