from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.runtime.dspy_bridge import (
    build_dspy_lm,
    import_dspy,
)
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import TeachingPacketRecord, WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError


class _GeneratedMcqDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    packet_ids: list[str] = Field(min_length=1, max_length=3)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanation: str = Field(min_length=1)


def _render_packet_context(record: TeachingPacketRecord) -> str:
    lines = [
        f"Packet title: {record.title}",
        f"Concept: {record.concept}",
        "Relationships:",
    ]
    for relationship in record.relationships:
        lines.append(f"- {relationship.statement}")
    lines.append("Snippets:")
    for snippet in record.snippets:
        lines.append(
            f"--- {snippet.path}:{snippet.start_line}-{snippet.end_line}"
        )
        lines.append(snippet.text)
    return "\n".join(lines)


def _generate_mcq_from_packets(
    *,
    packets: tuple[TeachingPacketRecord, ...],
    model: Any,
) -> _GeneratedMcqDraft:
    dspy = import_dspy()
    lm = build_dspy_lm(dspy=dspy, model=model)

    class GeneratePacketMcqSignature(dspy.Signature):
        """Generate one MCQ from the packet concept and relationships."""

        packet_context: str = dspy.InputField()
        question: str = dspy.OutputField(
            desc="One concise MCQ question about the concept or relationships."
        )
        option_a: str = dspy.OutputField(desc="Option 1.")
        option_b: str = dspy.OutputField(desc="Option 2.")
        option_c: str = dspy.OutputField(desc="Option 3.")
        option_d: str = dspy.OutputField(desc="Option 4.")
        correct_index: Literal[0, 1, 2, 3] = dspy.OutputField(
            desc="0 for option_a, 1 for option_b, 2 for option_c, 3 for option_d."
        )
        explanation: str = dspy.OutputField(
            desc="One short explanation of why the correct option is right."
        )

    predictor = dspy.Predict(GeneratePacketMcqSignature)
    predictor.set_lm(lm)
    prediction = predictor(
        packet_context="\n\n".join(
            _render_packet_context(packet) for packet in packets
        )
    )
    return _GeneratedMcqDraft(
        packet_ids=[packet.packet_id for packet in packets],
        question=str(prediction.question).strip(),
        options=[
            str(prediction.option_a).strip(),
            str(prediction.option_b).strip(),
            str(prediction.option_c).strip(),
            str(prediction.option_d).strip(),
        ],
        correct_index=int(prediction.correct_index),
        explanation=str(prediction.explanation).strip(),
    )


async def generate_mcq_from_teaching_packets(
    ctx: RunContext[WorkspaceDeps],
    packet_ids: Annotated[list[str], Field(min_length=1, max_length=3)],
) -> dict[str, object]:
    """Generate one MCQ draft from previously published teaching packets.

    Args:
        packet_ids: Teaching packet ids published earlier in this same run.
    """

    run_id = ctx.deps.session_scope.run_id
    model = ctx.deps.run_frame.model
    if run_id is None or model is None:
        raise ToolOperationalError(
            "generate_mcq_from_teaching_packets requires an active root "
            "session, run, and model"
        )
    normalized_packet_ids: list[str] = []
    for packet_id in packet_ids:
        if packet_id.strip() == "":
            raise ToolOperationalError(
                "generate_mcq_from_teaching_packets packet_ids must not "
                "contain blank values"
            )
        normalized_packet_ids.append(packet_id.strip())
    if len(set(normalized_packet_ids)) != len(normalized_packet_ids):
        raise ToolOperationalError(
            "generate_mcq_from_teaching_packets packet_ids must be unique"
        )
    try:
        packets = ctx.deps.teaching_packet_registry.resolve_for_run(
            packet_ids=tuple(normalized_packet_ids),
            run_id=run_id,
        )
    except KeyError as error:
        raise ToolOperationalError(
            "generate_mcq_from_teaching_packets requires packet_ids that "
            "refer to teaching packets published earlier in this same run"
        ) from error

    draft = _generate_mcq_from_packets(packets=packets, model=model)
    return make_tool_return(
        return_value=draft.model_dump(mode="json"),
        title="generate onboarding mcq",
        summary="generated MCQ draft",
        details=None,
        display_label="Onboard",
    )


GENERATE_MCQ_FROM_TEACHING_PACKETS_TOOL = Tool(
    generate_mcq_from_teaching_packets,
    takes_ctx=True,
    name="generate_mcq_from_teaching_packets",
    description=(
        "Generate one multiple-choice question draft from teaching packet ids "
        "published earlier in this same run. Returns packet_ids, question, "
        "four options, correct_index, and explanation so the result can be "
        "passed to ask_mcq_question."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = [
    "GENERATE_MCQ_FROM_TEACHING_PACKETS_TOOL",
    "generate_mcq_from_teaching_packets",
]
