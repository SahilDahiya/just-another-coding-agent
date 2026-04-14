from __future__ import annotations

import openai

from just_another_coding_agent.runtime.recovery import (
    is_retryable_run_error,
    should_retry_run_error,
)


def _stub(exc_type: type[BaseException]) -> BaseException:
    """Construct an exception instance for isinstance() checks without
    invoking the constructor's required-args validation.

    The OpenAI SDK exception classes require a real httpx Response to
    construct normally; the recovery classifier only cares about
    isinstance(), so __new__ is enough and avoids stubbing transport.
    """
    return exc_type.__new__(exc_type)


def test_internal_server_error_is_retryable() -> None:
    assert is_retryable_run_error(_stub(openai.InternalServerError))


def test_internal_server_error_retried_when_no_stream_event_yet() -> None:
    err = _stub(openai.InternalServerError)
    assert should_retry_run_error(error=err, saw_streamed_event=False, attempts=0)


def test_internal_server_error_not_retried_after_streamed_event() -> None:
    err = _stub(openai.InternalServerError)
    assert not should_retry_run_error(error=err, saw_streamed_event=True, attempts=0)


def test_internal_server_error_retry_limit_respected() -> None:
    err = _stub(openai.InternalServerError)
    assert not should_retry_run_error(error=err, saw_streamed_event=False, attempts=1)


def test_existing_retryable_types_still_retried() -> None:
    assert is_retryable_run_error(_stub(openai.APIConnectionError))
    assert is_retryable_run_error(_stub(openai.APITimeoutError))
    assert is_retryable_run_error(TimeoutError())


def test_non_retryable_error_types_are_not_retried() -> None:
    assert not is_retryable_run_error(ValueError("oops"))
    assert not is_retryable_run_error(_stub(openai.BadRequestError))
    assert not is_retryable_run_error(_stub(openai.AuthenticationError))
