from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from just_another_coding_agent.runtime.compaction import (
    build_in_run_history_processor,
    restore_in_run_compaction_from_messages,
)
from just_another_coding_agent.runtime.compaction.in_run import (
    IN_RUN_COMPACTION_METADATA_KEY,
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


async def test_restore_in_run_compaction_round_trips_original_tool_output() -> None:
    original = "\n".join(
        f"line-{index:04d} abcdefghijklmnopqrstuvwxyz" for index in range(80)
    )
    messages = _tool_history(original)

    compacted = await build_in_run_history_processor(soft_char_limit=120)(messages)
    compacted_tool_return = compacted[2].parts[0]

    assert isinstance(compacted_tool_return, ToolReturnPart)
    assert isinstance(compacted_tool_return.content, str)
    assert compacted_tool_return.content.startswith(
        "Compacted historical read result for big.txt"
    )
    assert isinstance(compacted_tool_return.metadata, dict)
    assert IN_RUN_COMPACTION_METADATA_KEY in compacted_tool_return.metadata

    restored = restore_in_run_compaction_from_messages(compacted)
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
                    metadata={IN_RUN_COMPACTION_METADATA_KEY: {}},
                )
            ]
        )
    ]

    try:
        restore_in_run_compaction_from_messages(messages)
    except RuntimeError as error:
        assert str(error) == "In-run compaction metadata is missing original_content"
    else:
        raise AssertionError("expected restore_in_run_compaction_from_messages to fail")
