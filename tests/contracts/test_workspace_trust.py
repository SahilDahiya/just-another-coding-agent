from pathlib import Path

from just_another_coding_agent.runtime import workspace_trust


def test_resolve_workspace_trust_target_uses_repo_root_for_nested_workspace(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workspace_root = repo_root / "nested" / "deeper"
    workspace_root.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    assert workspace_trust.resolve_workspace_trust_target(workspace_root) == repo_root


def test_resolve_workspace_trust_target_ignores_global_temp_git_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    temp_root = tmp_path / "global-temp"
    workspace_root = temp_root / "scratch" / "workspace"
    workspace_root.mkdir(parents=True)
    (temp_root / ".git").mkdir()
    monkeypatch.setattr(workspace_trust.tempfile, "gettempdir", lambda: str(temp_root))

    assert (
        workspace_trust.resolve_workspace_trust_target(workspace_root)
        == workspace_root
    )
