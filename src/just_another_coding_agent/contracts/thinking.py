from __future__ import annotations

from typing import Literal, TypeAlias

ThinkingSetting: TypeAlias = (
    bool
    | Literal[
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ]
)

__all__ = ["ThinkingSetting"]
