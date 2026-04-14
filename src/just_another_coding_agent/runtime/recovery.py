from __future__ import annotations

from collections.abc import Iterator

CANONICAL_RUN_RECOVERY_RETRY_LIMIT = 1


def _get_retryable_run_error_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [TimeoutError]
    try:
        import httpx

        types.append(httpx.TransportError)
    except ImportError:
        pass
    try:
        import openai

        types.append(openai.APIConnectionError)
        types.append(openai.APITimeoutError)
        types.append(openai.InternalServerError)
    except ImportError:
        pass
    return tuple(types)


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
    retryable_types = _get_retryable_run_error_types()
    return any(
        isinstance(current, retryable_types) for current in _iter_error_chain(error)
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
