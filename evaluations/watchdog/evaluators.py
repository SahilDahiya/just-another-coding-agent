from __future__ import annotations

from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext, HasMatchingSpan

from just_another_coding_agent.runtime.tracing import (
    TOOL_NAME_ATTRIBUTE,
    TOOL_SPAN_NAME,
)


def has_long_tool_span(
    *,
    tool_name: str,
    min_duration_seconds: float,
) -> HasMatchingSpan:
    return HasMatchingSpan(
        query={
            "name_equals": TOOL_SPAN_NAME,
            "has_attributes": {
                TOOL_NAME_ATTRIBUTE: tool_name,
            },
            "min_duration": min_duration_seconds,
        }
    )


@dataclass
class ShellHeavyWithoutEditsEvaluator(Evaluator[object, object, object]):
    min_shell_spans: int = 3

    def evaluate(self, ctx: EvaluatorContext[object, object, object]) -> bool:
        bash_spans = ctx.span_tree.find(
            {
                "name_equals": TOOL_SPAN_NAME,
                "has_attributes": {TOOL_NAME_ATTRIBUTE: "shell"},
            }
        )
        if len(bash_spans) < self.min_shell_spans:
            return False

        edit_like_spans = [
            *ctx.span_tree.find(
                {
                    "name_equals": TOOL_SPAN_NAME,
                    "has_attributes": {TOOL_NAME_ATTRIBUTE: "edit"},
                }
            ),
            *ctx.span_tree.find(
                {
                    "name_equals": TOOL_SPAN_NAME,
                    "has_attributes": {TOOL_NAME_ATTRIBUTE: "write"},
                }
            ),
        ]
        return not edit_like_spans


__all__ = ["ShellHeavyWithoutEditsEvaluator", "has_long_tool_span"]
