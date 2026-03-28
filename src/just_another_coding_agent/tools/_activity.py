from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ToolReturn

from just_another_coding_agent.contracts.run_events import ToolActivityDetails


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
) -> ToolReturn:
    metadata: dict[str, Any] = {
        "title": title,
        "summary": summary,
    }
    if details is not None:
        metadata["details"] = details.model_dump(mode="python")
    return ToolReturn(return_value=return_value, metadata=metadata)


__all__ = ["make_tool_return", "truncate_activity_label"]
