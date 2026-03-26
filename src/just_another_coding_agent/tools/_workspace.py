from __future__ import annotations

from pathlib import Path


def normalize_workspace_root(workspace_root: Path | str) -> Path:
    root = Path(workspace_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace root is not a directory: {root}")
    return root


def resolve_workspace_path(*, workspace_root: Path | str, tool_path: str) -> Path:
    root = normalize_workspace_root(workspace_root)
    candidate = Path(tool_path)
    if candidate.is_absolute():
        return candidate.resolve()

    return (root / candidate).resolve()


__all__ = ["normalize_workspace_root", "resolve_workspace_path"]
