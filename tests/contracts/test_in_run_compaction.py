from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.runtime.compaction import (
    restore_in_run_compaction_from_messages,
)
from just_another_coding_agent.runtime.compaction.in_run import (
    IN_RUN_COMPACTION_METADATA_KEY,
    build_in_run_compaction_controller,
    compact_in_run_message_history,
)


def _tool_history(content: str) -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content="inspect big file")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read",
                    args={"path": "big.txt"},
                    tool_call_id="call-read",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    content=content,
                    tool_call_id="call-read",
                )
            ]
        ),
    ]


def _two_tool_history(*, old_content: str, recent_content: str) -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content="inspect files")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read",
                    args={"path": "old.txt"},
                    tool_call_id="call-old",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    content=old_content,
                    tool_call_id="call-old",
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read",
                    args={"path": "recent.txt"},
                    tool_call_id="call-recent",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    content=recent_content,
                    tool_call_id="call-recent",
                )
            ]
        ),
    ]


async def test_restore_in_run_compaction_round_trips_original_tool_output() -> None:
    original = "\n".join(
        f"line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    )
    messages = _tool_history(original)

    controller = build_in_run_compaction_controller(soft_char_limit=120)
    compacted = await controller.apply(messages)
    compacted_tool_return = compacted[2].parts[0]

    assert isinstance(compacted_tool_return, ToolReturnPart)
    assert isinstance(compacted_tool_return.content, str)
    assert compacted_tool_return.content.startswith(
        "Compacted historical read result for big.txt"
    )
    assert isinstance(compacted_tool_return.metadata, dict)
    assert IN_RUN_COMPACTION_METADATA_KEY in compacted_tool_return.metadata

    restored = controller.restore(compacted)
    restored_tool_return = restored[2].parts[0]

    assert isinstance(restored_tool_return, ToolReturnPart)
    assert restored_tool_return.content == original
    assert restored_tool_return.metadata is None


def test_restore_in_run_compaction_fails_when_original_content_is_missing() -> None:
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    content="Compacted historical read result for big.txt: 80 lines.",
                    tool_call_id="call-read",
                    metadata={
                        IN_RUN_COMPACTION_METADATA_KEY: {"storage_key": "call-read"}
                    },
                )
            ]
        )
    ]

    try:
        restore_in_run_compaction_from_messages(messages)
    except RuntimeError as error:
        assert str(error) == "In-run compaction restore requires original content state"
    else:
        raise AssertionError("expected restore_in_run_compaction_from_messages to fail")


async def test_in_run_compaction_prefers_compacting_older_history_before_recent_tail(
) -> None:
    old_content = "\n".join(
        f"old-line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(120)
    )
    recent_content = "\n".join(
        f"recent-line-{index:04d} abcdefghijklmnopqrstuvwxyz"
        for index in range(24)
    )
    messages = _two_tool_history(
        old_content=old_content,
        recent_content=recent_content,
    )

    result = compact_in_run_message_history(
        messages,
        soft_char_limit=4_500,
    )

    assert result.was_compacted is True
    assert result.used_full_history_fallback is False
    assert result.compacted_tool_result_count == 1
    assert result.preserved_tail_start == 3

    old_return = result.messages[2].parts[0]
    recent_return = result.messages[4].parts[0]

    assert isinstance(old_return, ToolReturnPart)
    assert isinstance(recent_return, ToolReturnPart)
    assert isinstance(old_return.content, str)
    assert old_return.content.startswith(
        "Compacted historical read result for old.txt"
    )
    assert old_return.metadata is not None
    assert recent_return.content == recent_content
    assert recent_return.metadata is None


async def test_in_run_compaction_falls_back_when_old_prefix_compaction_is_not_enough(
) -> None:
    old_content = "\n".join(
        f"old-line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(120)
    )
    recent_content = "\n".join(
        f"recent-line-{index:04d} abcdefghijklmnopqrstuvwxyz"
        for index in range(120)
    )
    messages = _two_tool_history(
        old_content=old_content,
        recent_content=recent_content,
    )

    result = compact_in_run_message_history(
        messages,
        soft_char_limit=500,
    )

    assert result.was_compacted is True
    assert result.used_full_history_fallback is True
    assert result.compacted_tool_result_count == 2

    old_return = result.messages[2].parts[0]
    recent_return = result.messages[4].parts[0]

    assert isinstance(old_return, ToolReturnPart)
    assert isinstance(recent_return, ToolReturnPart)
    assert isinstance(old_return.content, str)
    assert isinstance(recent_return.content, str)
    assert old_return.content.startswith(
        "Compacted historical read result for old.txt"
    )
    assert recent_return.content.startswith(
        "Compacted historical read result for recent.txt"
    )
