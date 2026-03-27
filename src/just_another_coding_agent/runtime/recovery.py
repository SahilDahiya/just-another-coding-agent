from __future__ import annotations

from collections.abc import Iterator

import httpx
import openai

CANONICAL_RUN_RECOVERY_RETRY_LIMIT = 1

_RETRYABLE_RUN_ERROR_TYPES = (
    TimeoutError,
    httpx.TransportError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)


def should_retry_run_error(
    *,
    error: BaseException,
    saw_streamed_event: bool,
    attempts: int,
) -> bool:
    return (
        not saw_streamed_event
        and attempts < CANONICAL_RUN_RECOVERY_RETRY_LIMIT
        and is_retryable_run_error(error)
    )


def is_retryable_run_error(error: BaseException) -> bool:
    return any(
        isinstance(current, _RETRYABLE_RUN_ERROR_TYPES)
        for current in _iter_error_chain(error)
    )


def _iter_error_chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    stack: list[BaseException] = [error]

    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current

        if isinstance(current, BaseExceptionGroup):
            stack.extend(reversed(current.exceptions))

        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)


__all__ = [
    "CANONICAL_RUN_RECOVERY_RETRY_LIMIT",
    "is_retryable_run_error",
    "should_retry_run_error",
]
