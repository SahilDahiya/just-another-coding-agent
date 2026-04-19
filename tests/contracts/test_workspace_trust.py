from __future__ import annotations

from pathlib import Path

from just_another_coding_agent.runtime.workspace_trust import (
    resolve_workspace_trust_target,
)


def test_resolve_workspace_trust_target_uses_git_directory_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workspace_root = repo_root / "nested" / "child"
    workspace_root.mkdir(parents=True)
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    assert resolve_workspace_trust_target(workspace_root) == repo_root.resolve()


def test_resolve_workspace_trust_target_uses_gitdir_pointer_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workspace_root = repo_root / "nested"
    workspace_root.mkdir(parents=True)
    actual_git_dir = tmp_path / "worktrees" / "repo"
    actual_git_dir.mkdir(parents=True)
    (actual_git_dir / "HEAD").write_text(
        "ref: refs/heads/main\n",
        encoding="utf-8",
    )
    (repo_root / ".git").write_text(
        f"gitdir: {actual_git_dir}\n",
        encoding="utf-8",
    )

    assert resolve_workspace_trust_target(workspace_root) == repo_root.resolve()


def test_resolve_workspace_trust_target_ignores_arbitrary_dot_git_files(
    tmp_path: Path,
) -> None:
    fake_home = tmp_path / "home"
    repo_root = fake_home / "repo"
    workspace_root = repo_root / "nested"
    workspace_root.mkdir(parents=True)
    (fake_home / ".git").write_text("not a git pointer\n", encoding="utf-8")

    assert resolve_workspace_trust_target(workspace_root) == workspace_root.resolve()
