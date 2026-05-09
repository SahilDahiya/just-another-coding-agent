import asyncio

import pytest

from just_another_coding_agent.rpc.state import _FollowUpState
from tests.contracts.rpc_stdio_test_support import (
    noop_emit_queue_state,
    noop_emit_submitted_prompt_batch,
)


async def test_follow_up_state_interrupt_promotes_pending_steer_to_front() -> None:
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    await state.activate(
        "a" * 32,
        run_task=run_task,
        emit_queue_state=noop_emit_queue_state,
        emit_submitted_prompt_batch=noop_emit_submitted_prompt_batch,
    )
    await state.enqueue("a" * 32, "later prompt", mode="later")
    await state.activate_steer_boundary("a" * 32, lambda prompts: None)
    await state.enqueue("a" * 32, "steer prompt", mode="next")

    promoted_count = await state.interrupt(
        "a" * 32,
        promote_queued_steer=True,
    )

    assert promoted_count == 1
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch("a" * 32) == ["steer prompt"]
    assert await state.take_next_follow_up_batch("a" * 32) == ["later prompt"]


async def test_follow_up_state_interrupt_preserves_fifo_within_promoted_and_later() -> (
    None
):
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    session_id = "b" * 32
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=noop_emit_queue_state,
        emit_submitted_prompt_batch=noop_emit_submitted_prompt_batch,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.enqueue(session_id, "later two", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")
    await state.enqueue(session_id, "next two", mode="next")

    promoted_count = await state.interrupt(
        session_id,
        promote_queued_steer=True,
    )

    assert promoted_count == 2
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch(session_id) == [
        "next one",
        "next two",
    ]
    assert await state.take_next_follow_up_batch(session_id) == [
        "later one",
        "later two",
    ]


async def test_follow_up_state_downgrades_pending_next_ahead_of_existing_later() -> (
    None
):
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    session_id = "c" * 32
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=noop_emit_queue_state,
        emit_submitted_prompt_batch=noop_emit_submitted_prompt_batch,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")
    await state.enqueue(session_id, "next two", mode="next")

    await state.downgrade_pending_steers_to_follow_ups(session_id)

    assert await state.take_next_follow_up_batch(session_id) == [
        "next one",
        "next two",
    ]
    assert await state.take_next_follow_up_batch(session_id) == ["later one"]
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task


async def test_follow_up_state_interrupt_without_promotion_preserves_later_only() -> (
    None
):
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    session_id = "d" * 32
    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=noop_emit_queue_state,
        emit_submitted_prompt_batch=noop_emit_submitted_prompt_batch,
    )
    await state.enqueue(session_id, "later one", mode="later")
    await state.activate_steer_boundary(session_id, lambda prompts: None)
    await state.enqueue(session_id, "next one", mode="next")

    promoted_count = await state.interrupt(
        session_id,
        promote_queued_steer=False,
    )

    assert promoted_count == 0
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert await state.take_next_follow_up_batch(session_id) == ["later one"]


async def test_follow_up_state_submit_active_boundary_emits_submitted_next() -> None:
    state = _FollowUpState()
    run_task = asyncio.create_task(asyncio.Event().wait())
    session_id = "e" * 32
    captured: dict[str, object] = {"queue_events": [], "submitted": []}

    async def _emit_queue_state(event) -> None:
        captured["queue_events"].append(event.model_dump(mode="json"))

    async def _emit_submitted(mode: str, prompts: list[str]) -> None:
        captured["submitted"].append({"mode": mode, "prompts": prompts})

    target: list[list[str]] = []

    await state.activate(
        session_id,
        run_task=run_task,
        emit_queue_state=_emit_queue_state,
        emit_submitted_prompt_batch=_emit_submitted,
    )
    await state.activate_steer_boundary(session_id, target.append)
    await state.enqueue(session_id, "next one", mode="next")
    await state.enqueue(session_id, "next two", mode="next")

    await state.submit_active_steer_boundary(session_id)

    assert target == [["next one", "next two"]]
    assert captured["submitted"] == [
        {"mode": "next", "prompts": ["next one", "next two"]}
    ]
    assert captured["queue_events"][-1] == {
        "type": "session_queue_state",
        "next_prompts": [],
        "later_prompts": [],
    }
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
