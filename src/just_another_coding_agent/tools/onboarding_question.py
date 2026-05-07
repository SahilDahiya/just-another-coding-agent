from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.onboarding import (
    PublishedMcqQuestion,
    publish_onboarding_mcq,
)
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError


async def ask_onboarding_question(
    ctx: RunContext[WorkspaceDeps],
    question: Annotated[str, Field(min_length=1)],
    options: Annotated[list[str], Field(min_length=4, max_length=4)],
    correct_index: Annotated[int, Field(ge=0, le=3)],
    evidence: Annotated[list[str], Field(min_length=1)],
    explanation: Annotated[str, Field(min_length=1)],
) -> dict[str, object]:
    """Ask the user one onboarding multiple-choice question and return the result.

    Args:
        question: The question text shown to the user.
        options: Four concise and unique answer options.
        correct_index: Zero-based index of the correct option in options.
        evidence: Workspace file paths that justify the question.
        explanation: Short feedback shown after the user answers.
    """

    requester = ctx.deps.onboarding_question_requester
    if requester is None:
        raise ToolOperationalError(
            "Onboarding question requests are unavailable in this runtime"
        )
    sessions_root = ctx.deps.sessions_root
    session_id = ctx.deps.session_scope.session_id
    run_id = ctx.deps.session_scope.run_id
    if sessions_root is None or session_id is None or run_id is None:
        raise ToolOperationalError(
            "Onboarding question tool requires an active root session and run"
        )

    request = publish_onboarding_mcq(
        sessions_root=sessions_root,
        workspace_root=ctx.deps.workspace_root,
        session_id=session_id,
        run_id=run_id,
        question=PublishedMcqQuestion(
            question_type="mcq",
            prompt=question,
            options=tuple(options),
            correct_index=correct_index,
            evidence=tuple(evidence),
            explanation=explanation,
        ),
    )
    answer = await requester(request)
    return make_tool_return(
        return_value=answer.model_dump(mode="json"),
        title="ask onboarding question",
        summary=(
            "user answered correctly"
            if answer.is_correct
            else "user answered incorrectly"
        ),
        details=None,
        display_label="Onboard",
    )


ASK_ONBOARDING_QUESTION_TOOL = Tool(
    ask_onboarding_question,
    takes_ctx=True,
    name="ask_onboarding_question",
    description=(
        "Present one onboarding multiple-choice question, wait for the user's "
        "selection, persist it, and return the result. Supply four options, a "
        "zero-based correct_index, evidence file paths, and a short "
        "explanation. Do not reveal the correct answer before calling the tool."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = ["ASK_ONBOARDING_QUESTION_TOOL", "ask_onboarding_question"]
