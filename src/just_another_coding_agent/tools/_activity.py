from __future__ import annotations

import os
from typing import Any

from pydantic_ai.messages import ToolReturn

from just_another_coding_agent.contracts.run_events import ToolActivityDetails


def shorten_path(path: str | None, workspace_root: str) -> str | None:
    """Return a workspace-relative short path, or basename for outside paths."""
    if path is None:
        return None
    if os.path.isabs(path):
        abs_path = os.path.abspath(path)
    else:
        # Resolve relative paths against workspace root, not cwd
        abs_path = os.path.abspath(os.path.join(workspace_root, path))
    abs_root = os.path.abspath(workspace_root)
    if abs_path.startswith(abs_root + os.sep):
        return abs_path[len(abs_root) + 1 :].replace("\\", "/")
    if abs_path == abs_root:
        return "."
    # Outside workspace — return basename
    return (os.path.basename(abs_path) or path).replace("\\", "/")


def truncate_activity_label(text: str, *, limit: int = 56) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def make_tool_return(
    *,
    return_value: Any,
    title: str,
    summary: str | None,
    details: ToolActivityDetails | None,
    display_label: str | None = None,
) -> ToolReturn:
    metadata: dict[str, Any] = {
        "title": title,
        "summary": summary,
    }
    if display_label is not None:
        metadata["display_label"] = display_label
    if details is not None:
        metadata["details"] = details.model_dump(mode="python")
    return ToolReturn(return_value=return_value, metadata=metadata)


__all__ = ["make_tool_return", "shorten_path", "truncate_activity_label"]
