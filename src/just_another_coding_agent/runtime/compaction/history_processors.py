from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.runtime.models import (
    build_in_run_compaction_soft_char_limit,
)

from .in_run import build_in_run_history_processor

type ModelHistoryProcessor = Callable[
    [list[ModelMessage]],
    list[ModelMessage] | Awaitable[list[ModelMessage]],
]


def build_compaction_history_processors(
    *,
    model: Any,
    history_processors: Sequence[ModelHistoryProcessor] | None = None,
) -> list[ModelHistoryProcessor]:
    effective_history_processors = list(history_processors or [])
    effective_history_processors.append(
        build_in_run_history_processor(
            soft_char_limit=build_in_run_compaction_soft_char_limit(model)
        )
    )
    return effective_history_processors


__all__ = ["ModelHistoryProcessor", "build_compaction_history_processors"]
