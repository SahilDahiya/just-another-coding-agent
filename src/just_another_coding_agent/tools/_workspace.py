from __future__ import annotations

import os
from pathlib import Path


def normalize_workspace_root(workspace_root: Path | str) -> Path:
    root = Path(workspace_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace root is not a directory: {root}")
    return root


def absolutize_workspace_path(*, workspace_root: Path | str, tool_path: str) -> Path:
    root = normalize_workspace_root(workspace_root)
    candidate = Path(tool_path)
    if candidate.is_absolute():
        return Path(os.path.abspath(str(candidate)))

    return Path(os.path.abspath(str(root / candidate)))


def canonicalize_path_target(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.exists():
        return candidate.resolve()

    absolute_candidate = Path(os.path.abspath(str(candidate)))
    existing_parent = absolute_candidate.parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not existing_parent.exists():
        return absolute_candidate
    relative_suffix = absolute_candidate.relative_to(existing_parent)
    return existing_parent.resolve() / relative_suffix


def resolve_workspace_path(*, workspace_root: Path | str, tool_path: str) -> Path:
    return canonicalize_path_target(
        absolutize_workspace_path(
            workspace_root=workspace_root,
            tool_path=tool_path,
        )
    )


def path_is_within_workspace(
    *,
    workspace_root: Path | str,
    resolved_path: Path,
) -> bool:
    root = normalize_workspace_root(workspace_root)
    return resolved_path.is_relative_to(root)


__all__ = [
    "absolutize_workspace_path",
    "canonicalize_path_target",
    "normalize_workspace_root",
    "path_is_within_workspace",
    "resolve_workspace_path",
]
