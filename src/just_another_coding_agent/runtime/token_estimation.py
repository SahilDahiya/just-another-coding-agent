"""Fast token estimation using a chars-per-token heuristic.

Provides O(1) cost estimates suitable for budget checks in compaction
triggers and replacement-history sizing.  For accurate token counts
(e.g. precise compaction threshold decisions), use
``runtime.compaction.token_counting`` instead.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

from just_another_coding_agent.contracts.compaction import (
    COMPACTION_CHARS_PER_TOKEN_HEURISTIC,
)

_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])
_ESTIMATION_METHOD = "chars_per_token_v1"


@dataclass(frozen=True)
class TokenEstimate:
    estimated_tokens: int
    estimation_method: str


def estimate_text_tokens(*, model: Any, text: str | None) -> TokenEstimate:
    del model
    if not text:
        return TokenEstimate(estimated_tokens=0, estimation_method=_ESTIMATION_METHOD)
    return TokenEstimate(
        estimated_tokens=math.ceil(
            len(text) / COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        ),
        estimation_method=_ESTIMATION_METHOD,
    )


def estimate_messages_tokens(
    *,
    model: Any,
    messages: Sequence[ModelMessage],
) -> TokenEstimate:
    del model
    if not messages:
        return TokenEstimate(estimated_tokens=0, estimation_method=_ESTIMATION_METHOD)
    serialized = json.dumps(
        _MODEL_MESSAGES_ADAPTER.dump_python(list(messages), mode="json"),
        ensure_ascii=False,
    )
    return TokenEstimate(
        estimated_tokens=math.ceil(
            len(serialized) / COMPACTION_CHARS_PER_TOKEN_HEURISTIC
        ),
        estimation_method=_ESTIMATION_METHOD,
    )


__all__ = [
    "TokenEstimate",
    "estimate_messages_tokens",
    "estimate_text_tokens",
]
