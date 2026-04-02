from __future__ import annotations

from just_another_coding_agent.runtime.compaction.constants import (
    SESSION_COMPACTION_OUTPUT_RESERVE_TOKENS,
)


def build_effective_compaction_context_window_tokens(
    context_window_tokens: int,
) -> int:
    reserve_tokens = min(
        SESSION_COMPACTION_OUTPUT_RESERVE_TOKENS,
        max(context_window_tokens // 4, 1),
    )
    return max(context_window_tokens - reserve_tokens, 0)


__all__ = ["build_effective_compaction_context_window_tokens"]
