"""Minimal reproducer for a pydantic-ai bug.

`pydantic_ai.capture_run_messages` is a generator-based context manager
that calls `_messages_ctx_var.set(messages)` on enter and
`_messages_ctx_var.reset(token)` on exit. When used inside an async
generator that is later closed from a different asyncio Context than the
one where the body started executing, the reset call raises:

    ValueError: <Token var=<ContextVar ...> ...> was created in a
    different Context

This script demonstrates the failure with no third-party dependencies
beyond pydantic-ai itself, and also shows that the same context manager
behaves correctly when used outside an async generator.

Run with:
    uv run python scripts/repro_pydantic_ai_capture_run_messages_ctxvar.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback

import pydantic_ai
from pydantic_ai import capture_run_messages


async def _generator_with_capture():
    """An async generator whose body wraps its yields in capture_run_messages."""
    with capture_run_messages() as messages:  # noqa: F841
        try:
            yield "first"
            yield "second"
            yield "third"
        finally:
            print("[gen] body finally about to run", flush=True)


async def case_async_generator_close_from_other_task() -> bool:
    """Repro: aclose() called from a task different from the iterator.

    Returns True if the bug reproduced (ValueError raised), False otherwise.
    """
    print("=== Case 1: async generator + aclose from a different task ===", flush=True)
    agen = _generator_with_capture()

    # Drive the first yield from the current task. capture_run_messages.__enter__
    # runs in this task's Context and sets the contextvar token here.
    first = await agen.__anext__()
    print(f"[main] first yield = {first!r}", flush=True)

    async def _close_from_other_task() -> None:
        # This task has its OWN Context (a copy taken at task-creation time).
        # When aclose() resumes the generator body, the with block's __exit__
        # calls _messages_ctx_var.reset(token), but `token` was created in
        # the parent task's Context, not this one.
        await agen.aclose()

    closer_task = asyncio.create_task(_close_from_other_task())
    try:
        await closer_task
    except ValueError as e:
        print(
            f"[main] CAUGHT ValueError from closer task: {e!r}",
            flush=True,
        )
        return "different Context" in str(e)
    except Exception as e:  # noqa: BLE001
        print(f"[main] unexpected exception: {type(e).__name__}: {e}", flush=True)
        return False

    # The exception may also surface as an unraised task exception via the
    # loop's default exception handler rather than as a sync raise from await.
    if closer_task.done():
        exc = closer_task.exception()
        if isinstance(exc, ValueError) and "different Context" in str(exc):
            print(f"[main] closer task exception: {exc!r}", flush=True)
            return True
    return False


async def case_async_generator_close_from_same_task() -> bool:
    """Control: aclose() called from the same task that iterated.

    Should NOT reproduce the bug, because __enter__ and __exit__ run in
    the same Context.
    """
    print("=== Case 2: async generator + aclose from same task ===", flush=True)
    agen = _generator_with_capture()
    first = await agen.__anext__()
    print(f"[main] first yield = {first!r}", flush=True)
    try:
        await agen.aclose()
    except ValueError as e:
        print(f"[main] UNEXPECTED ValueError: {e!r}", flush=True)
        return False
    print("[main] aclose() completed cleanly", flush=True)
    return True


async def case_plain_async_function() -> bool:
    """Control: capture_run_messages used outside an async generator.

    Should NOT reproduce the bug; this is the supported usage.
    """
    print("=== Case 3: capture_run_messages in a plain async function ===", flush=True)
    try:
        with capture_run_messages() as messages:  # noqa: F841
            await asyncio.sleep(0)
    except ValueError as e:
        print(f"[main] UNEXPECTED ValueError: {e!r}", flush=True)
        return False
    print("[main] plain usage completed cleanly", flush=True)
    return True


async def main() -> int:
    print(f"pydantic-ai version: {pydantic_ai.__version__}", flush=True)
    print(f"python version: {sys.version}", flush=True)
    print(flush=True)

    bug_reproduced = await case_async_generator_close_from_other_task()
    print(flush=True)
    same_task_ok = await case_async_generator_close_from_same_task()
    print(flush=True)
    plain_ok = await case_plain_async_function()
    print(flush=True)

    print("=== Summary ===", flush=True)
    print(f"  Case 1 (bug reproduced):   {bug_reproduced}", flush=True)
    print(f"  Case 2 (same task ok):     {same_task_ok}", flush=True)
    print(f"  Case 3 (plain async ok):   {plain_ok}", flush=True)

    if bug_reproduced and same_task_ok and plain_ok:
        print(
            "\nResult: bug is reproduced, controls pass — "
            "this is a real, narrowly-scoped issue.",
            flush=True,
        )
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
