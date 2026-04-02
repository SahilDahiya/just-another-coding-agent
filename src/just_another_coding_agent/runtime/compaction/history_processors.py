from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.runtime.models import (
    build_in_run_compaction_soft_char_limit,
)

from .in_run import InRunCompactionResult, build_in_run_compaction_controller

type ModelHistoryProcessor = Callable[
    [list[ModelMessage]],
    list[ModelMessage] | Awaitable[list[ModelMessage]],
]


@dataclass(frozen=True)
class CompactionHistoryRuntime:
    history_processors: list[ModelHistoryProcessor]
    restore_messages: Callable[[list[ModelMessage]], list[ModelMessage]]


def build_compaction_history_runtime(
    *,
    model: Any,
    history_processors: Sequence[ModelHistoryProcessor] | None = None,
    on_in_run_compaction_applied: (
        Callable[[InRunCompactionResult], None] | None
    ) = None,
) -> CompactionHistoryRuntime:
    controller = build_in_run_compaction_controller(
        soft_char_limit=build_in_run_compaction_soft_char_limit(model),
        on_applied=on_in_run_compaction_applied,
    )
    effective_history_processors = list(history_processors or [])
    effective_history_processors.append(controller.apply)
    return CompactionHistoryRuntime(
        history_processors=effective_history_processors,
        restore_messages=controller.restore,
    )


def build_compaction_history_processors(
    *,
    model: Any,
    history_processors: Sequence[ModelHistoryProcessor] | None = None,
) -> list[ModelHistoryProcessor]:
    return build_compaction_history_runtime(
        model=model,
        history_processors=history_processors,
    ).history_processors


__all__ = [
    "CompactionHistoryRuntime",
    "ModelHistoryProcessor",
    "build_compaction_history_processors",
    "build_compaction_history_runtime",
]
